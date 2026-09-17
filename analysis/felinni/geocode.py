"""Optional geocoding of the free-text `location` field.

Geocoding is opt-in and network-based (OpenStreetMap Nominatim via geopy),
so it's kept out of the hot path: most analyses (frequency, streaks,
seasonality, people) work on the raw location string alone. Only mapping
and "radius of life" need lat/lon, and those call `geocode_locations`
explicitly.

Results are cached to disk (default `data/geocode_cache.json`) so repeat
runs never re-query the same address, and Nominatim's usage policy (max
~1 request/sec, meaningful User-Agent) is respected. Nominatim is a free,
best-effort geocoder and it does get things wrong, especially for short or
building-only names ("B27 Terrace" landing in India) or even well-known
places when its ranking picks the wrong same-named/similar-token match
worldwide ("Santa Monica Pier" landing in Europe). Three layers push back
on that, in order of precedence: `geocode_overrides.json` (exact answers
you provide, always win and never touch the network), `anchors` (extra
context appended to the query for locations whose name/category matches a
keyword), and an adaptive region bias (queries are nudged toward wherever
your *other* locations this run already resolved to, without excluding
genuinely distant results like real trips).

Two query-shape fixups run before any of that: a location that already
looks like a complete mailing address (has its own ZIP or "United States")
skips the anchor entirely - appending redundant context to an
already-complete address doesn't help and can make Nominatim's parser fail
outright instead of just ignoring the extra text - and a missing comma
between the street and city (common when a calendar app flattens a
multi-line map address onto one line, e.g. "...Strathmore Dr Los Angeles,
CA...") is inserted back, since Nominatim leans heavily on commas to tell
address components apart. Neither fixes a business name glued directly onto
its own street number with no separator at all ("101 Boxing Club 1714
Newbury Rd...") - Nominatim's free-text search can still choke on which
number is the real house number there, and that one still needs a manual
override.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_CACHE_PATH = _DATA_DIR / "geocode_cache.json"

# Permanent manual corrections: {location string: {"lat":.., "lon":.., "display_name": "..."}}.
# Entries here always win over both the cache and a fresh Nominatim lookup,
# and that location is never sent over the network. Use this for anything
# anchors/bias still can't fix - gitignored, since it may reflect your
# actual addresses.
DEFAULT_OVERRIDES_PATH = _DATA_DIR / "geocode_overrides.json"

# Why each currently-unresolved location failed on its last attempt (e.g.
# "timed out after 10s", "Nominatim found no match for this query",
# "Nominatim service error: ..."). Written by `geocode_locations` alongside
# the cache so a run that leaves locations unresolved isn't a silent dead
# end - read it back with `load_diagnostics` to see why, one reason per
# location, instead of just a raw count of "not geocoded yet."
DEFAULT_DIAGNOSTICS_PATH = _DATA_DIR / "geocode_diagnostics.json"

# {location: anchor query text} for every location that couldn't be
# resolved on its own but was placed at a dict-form anchor's own
# coordinates instead (see `geocode_locations` - e.g. "Boelter 5800" ends
# up pinned at "UCLA, Los Angeles, CA" rather than left off the map
# entirely). Kept separate from `DEFAULT_DIAGNOSTICS_PATH`: these DO have a
# usable (if approximate) pin, so they're not a "failure" the way an
# unresolved location is - just worth flagging as inexact.
DEFAULT_APPROXIMATIONS_PATH = _DATA_DIR / "geocode_approximations.json"

# A building/room name alone ("North Campus Student Center") often won't
# geocode correctly, or geocodes to a same-named place somewhere else in
# the world entirely - even with anchor text appended to the query,
# Nominatim's ranking sometimes still prefers an unrelated same-named
# match elsewhere ("Royce Hall" is common enough to exist outside LA too).
# When a location's category OR the location text itself matches one of
# these keys (case-insensitive substring), the anchor kicks in - never
# touching the stored/displayed location, only the GEOCODING QUERY (and,
# for a dict anchor, which results are even considered):
#   - a plain string: appended to the query as context, e.g. "North Campus
#     Student Center, UCLA, Los Angeles, CA" - a nudge, not a guarantee.
#   - a dict {"query":, "lat":, "lon":, "radius_km":}: same text appended,
#     PLUS the search is restricted (bounded=True) to within that radius
#     of (lat, lon) - a hard guarantee the result is actually on campus,
#     not just a same-named building somewhere else in the world. Prefer
#     this form once you know your campus/workplace's coordinates.
# Edit this to match your own campus/workplace calendars.
DEFAULT_LOCATION_ANCHORS = {
    "ucla": {"query": "UCLA, Los Angeles, CA", "lat": 34.0689, "lon": -118.4452, "radius_km": 3.0},
    "amgen": {"query": "Amgen, Thousand Oaks, CA", "lat": 34.2064, "lon": -118.8253, "radius_km": 2.0},
}


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_cache(cache_path: Path) -> dict:
    return _load_json(cache_path)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _save_cache(cache_path: Path, cache: dict) -> None:
    _save_json(cache_path, cache)


def load_diagnostics(path: str | Path = DEFAULT_DIAGNOSTICS_PATH) -> dict[str, str]:
    """{location: reason} for every location that failed to geocode on its
    last attempt - see `geocode_locations`. Empty if nothing's failed (or
    nothing's been run yet)."""
    return _load_json(Path(path))


def load_approximations(path: str | Path = DEFAULT_APPROXIMATIONS_PATH) -> dict[str, str]:
    """{location: anchor query text} for every location placed at an
    anchor's own coordinates rather than its own resolved address - see
    `geocode_locations` and DEFAULT_APPROXIMATIONS_PATH."""
    return _load_json(Path(path))


def effective_cache(
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    overrides_path: str | Path = DEFAULT_OVERRIDES_PATH,
) -> dict:
    """The geocode cache with manual overrides layered on top - what the map
    (and anything else reading location coordinates) should actually use.
    Overrides always win, and this never touches the network, so a
    correction saved via `save_override` shows up immediately without
    waiting for a full `geocode_locations` run."""
    cache = dict(_load_cache(Path(cache_path)))
    overrides = _load_json(Path(overrides_path))
    for loc, value in overrides.items():
        if isinstance(value, dict) and isinstance(value.get("lat"), (int, float)) and isinstance(value.get("lon"), (int, float)):
            cache[loc] = value
    return cache


def save_override(
    location: str,
    entry: dict,
    overrides_path: str | Path = DEFAULT_OVERRIDES_PATH,
) -> None:
    """Records a manual correction for `location` - used by the Map tab's
    "fix a location" form. `entry` must at least have numeric "lat"/"lon";
    "display_name" is kept if present so the map popup still shows an
    address. Always wins over both the cache and future Nominatim lookups
    for that exact location string."""
    if not isinstance(entry.get("lat"), (int, float)) or not isinstance(entry.get("lon"), (int, float)):
        raise ValueError("override entry needs numeric lat/lon")
    overrides_path = Path(overrides_path)
    overrides = _load_json(overrides_path)
    overrides[location] = entry
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text(json.dumps(overrides, indent=2, sort_keys=True))


DEFAULT_TIMEOUT_SECONDS = 10.0


def geocode_one(query: str, user_agent: str = "felinni-calendar-analysis", timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict | None:
    """One-off live geocode of a free-text address/place, e.g. a corrected
    location a user typed into the Map tab. Not cached or rate-limited -
    meant for a single interactive lookup, not a bulk run."""
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderServiceError
    except ImportError as e:
        raise ImportError("geocode_one requires geopy: pip install geopy") from e

    geolocator = Nominatim(user_agent=user_agent, timeout=timeout)
    try:
        result = geolocator.geocode(query, addressdetails=True)
    except GeocoderServiceError:
        result = None
    if not result:
        return None

    address = (result.raw or {}).get("address", {}) if hasattr(result, "raw") else {}
    city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or address.get("county")
    neighbourhood = address.get("suburb") or address.get("neighbourhood") or address.get("quarter") or address.get("city_district") or address.get("borough")
    return {
        "lat": result.latitude, "lon": result.longitude, "display_name": result.address,
        "city": city, "country": address.get("country"), "neighbourhood": neighbourhood,
    }


def clear_cache_entries(locations: list[str], cache_path: str | Path = DEFAULT_CACHE_PATH) -> int:
    """Removes specific locations from the geocode cache so the next
    `geocode_locations` run re-fetches them - use this to correct a wrong
    result without deleting the whole cache. Returns how many were found
    and removed."""
    cache_path = Path(cache_path)
    cache = _load_cache(cache_path)
    removed = 0
    for loc in locations:
        if loc in cache:
            del cache[loc]
            removed += 1
    if removed:
        _save_cache(cache_path, cache)
    return removed


def clear_diagnostics_entry(location: str, path: str | Path = DEFAULT_DIAGNOSTICS_PATH) -> None:
    """Drops `location` from the failure diagnostics, e.g. once it's been
    fixed via a manual override - otherwise it would still show up as
    "failed" (from its last real attempt) even though it no longer needs
    one, since an override never touches diagnostics itself."""
    path = Path(path)
    diagnostics = _load_json(path)
    if location in diagnostics:
        del diagnostics[location]
        _save_json(path, diagnostics)


def clear_approximation_entry(location: str, path: str | Path = DEFAULT_APPROXIMATIONS_PATH) -> None:
    """Drops `location` from the approximate-placement list, e.g. once a
    manual override gives it a real (non-anchor) position."""
    path = Path(path)
    approximations = _load_json(path)
    if location in approximations:
        del approximations[location]
        _save_json(path, approximations)


_COMPLETE_ADDRESS_RE = re.compile(r"\b\d{5}(-\d{4})?\b|\bunited states\b|\busa\b", re.IGNORECASE)


def _looks_like_complete_address(loc: str) -> bool:
    """True once `loc` already carries its own city/state/ZIP/country -
    i.e. it's a full mailing address, not a bare building/room name. An
    anchor exists to give a BARE name ("North Campus Student Center")
    enough context to resolve - appending that same context to an address
    that's already complete doesn't help and can actively break it:
    Nominatim's free-text parser tries to fit every token into one
    coherent address, and a trailing "..., UCLA, Los Angeles, CA" tacked
    onto a query that already ends in its own "..., Los Angeles, CA,
    United States" gives it two conflicting endings to reconcile, which
    can fail outright instead of just being ignored."""
    return bool(_COMPLETE_ADDRESS_RE.search(loc))


# Calendar apps often hand back a location as "<name> <street address>" with
# the multi-line address (as shown in a map picker) flattened onto one line -
# and the line break between the street and the city sometimes turns into a
# bare space instead of a comma ("...Newbury Park Dr Newbury Park, CA..."
# has one but "...11024 Strathmore Dr Los Angeles, CA..." doesn't). Nominatim
# leans heavily on commas to separate address components, so a query missing
# just this one comma can fail to resolve even though the address itself is
# perfectly real - inserting it back is usually enough to fix that.
_STREET_SUFFIXES = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Way|Ct|Court|"
    r"Pl|Place|Plaza|Cir|Circle|Pkwy|Parkway|Ter|Terrace|Hwy|Highway|Sq|Square"
)
_MISSING_CITY_COMMA_RE = re.compile(
    rf"\b({_STREET_SUFFIXES})\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*),\s*([A-Z]{{2}})\b"
)


def _insert_missing_city_comma(text: str) -> str:
    return _MISSING_CITY_COMMA_RE.sub(r"\1, \2, \3", text)


def _anchor_for(loc: str, category: str | None, anchors: dict) -> str | dict | None:
    if _looks_like_complete_address(loc):
        return None
    haystacks = [loc.lower()] + ([category.lower()] if category else [])
    for key, value in anchors.items():
        if any(key.lower() in h for h in haystacks):
            return value
    return None


def _anchor_query_text(anchor: str | dict | None) -> str | None:
    if isinstance(anchor, dict):
        return anchor.get("query")
    return anchor


def _anchor_viewbox(anchor: str | dict | None):
    """A hard bounding box around a dict-form anchor's coordinates, or
    None for a plain-string anchor (query-text nudge only, no
    restriction) - see DEFAULT_LOCATION_ANCHORS."""
    if not isinstance(anchor, dict):
        return None
    lat, lon = anchor.get("lat"), anchor.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    radius_km = anchor.get("radius_km", 3.0)
    pad_degrees = radius_km / 111.0  # ~111km per degree of latitude, close enough for a small campus-sized radius
    return [(lat - pad_degrees, lon - pad_degrees), (lat + pad_degrees, lon + pad_degrees)]


def _viewbox_from_points(points: list[tuple[float, float]], pad_degrees: float = 3.0):
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return [
        (min(lats) - pad_degrees, min(lons) - pad_degrees),
        (max(lats) + pad_degrees, max(lons) + pad_degrees),
    ]


def geocode_locations(
    locations: list[str],
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    overrides_path: str | Path = DEFAULT_OVERRIDES_PATH,
    diagnostics_path: str | Path = DEFAULT_DIAGNOSTICS_PATH,
    approximations_path: str | Path = DEFAULT_APPROXIMATIONS_PATH,
    user_agent: str = "felinni-calendar-analysis",
    rate_limit_seconds: float = 1.0,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    on_progress=None,
    location_categories: dict[str, str] | None = None,
    anchors: dict[str, str] = DEFAULT_LOCATION_ANCHORS,
    force: bool = False,
) -> dict[str, dict | None]:
    """Geocode a list of unique location strings, returning
    {location: {"lat": ..., "lon": ..., "display_name": ...} or None}.

    Requires the optional `geopy` dependency and network access. Already
    cached locations are never re-queried by default (use
    `clear_cache_entries` to force just one, or `force=True` here to
    re-query every location in this run). If given, `on_progress(done,
    total)` is called once up front with done=0 and again after each
    location, so a CLI or web caller can show progress on what can be a
    multi-minute run (~1 request/sec). Locations with a manual override
    are never re-queried regardless of `force` - overrides always win and
    never touch the network.

    `timeout` is how long to wait for each individual Nominatim response
    before giving up on it (a `GeocoderTimedOut`, caught below like any
    other failure) - deliberately generous, since geopy's own default is a
    mere 1 second, far too short for Nominatim's real-world latency under
    any load. A too-short timeout doesn't fail loudly - it just quietly
    caches a string of otherwise-perfectly-geocodable locations as
    unresolved, which is easy to mistake for "there's nothing there" (the
    location string was too vague, etc.) rather than "the request would
    have worked if it'd been allowed to finish."

    `location_categories` (location -> category, e.g. from
    `df.groupby("location")["category"].agg(...)`) lets a bare building
    name get an anchor from `anchors` appended to the search query - see
    DEFAULT_LOCATION_ANCHORS. Independently, once a handful of locations
    in this run have resolved, later ambiguous queries are biased (not
    restricted) toward the region those already cover.

    If a location still can't be resolved on its own (even with an anchor's
    context appended) but did match a dict-form anchor with known
    coordinates, it's placed at that anchor's own coordinates rather than
    left off the map entirely - e.g. "Boelter 5800" (a UCLA room number
    that isn't its own addressable point) ends up pinned at "UCLA, Los
    Angeles, CA" instead of nowhere. This is recorded to
    `approximations_path` (see `load_approximations`), not the failure
    diagnostics, since it does now have a usable pin - just an inexact one.

    Every location this run leaves unresolved gets a reason recorded to
    `diagnostics_path` (see `load_diagnostics`) - a timeout, a Nominatim
    service error (rate-limited/blocked - the message usually names the
    HTTP status), or "found no match for this query" (Nominatim responded
    normally but had nothing for it - often a too-vague/building-only
    name, see the module docstring). Without this, "N locations aren't
    geocoded" gives no way to tell a systemic problem (everything timing
    out or getting blocked) from a pile of genuinely unmatchable location
    strings - which need different fixes (raise the timeout / wait out a
    block, vs. add an anchor or a manual override).
    """
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderServiceError, GeocoderTimedOut
    except ImportError as e:
        raise ImportError(
            "geocode_locations requires geopy: pip install geopy"
        ) from e

    cache_path = Path(cache_path)
    cache = _load_cache(cache_path)
    overrides = _load_json(Path(overrides_path))
    diagnostics_path = Path(diagnostics_path)
    diagnostics = _load_json(diagnostics_path)
    approximations_path = Path(approximations_path)
    approximations = _load_json(approximations_path)
    geolocator = Nominatim(user_agent=user_agent, timeout=timeout)
    location_categories = location_categories or {}

    # Overrides are applied unconditionally, including over a previously
    # cached (possibly wrong) result, and never touch the network. Values
    # are validated (not just trusted) since this file is hand-edited.
    for loc, value in overrides.items():
        if isinstance(value, dict) and isinstance(value.get("lat"), (int, float)) and isinstance(value.get("lon"), (int, float)):
            cache[loc] = value

    resolved_points = [(v["lat"], v["lon"]) for v in cache.values() if isinstance(v, dict)]

    if force:
        # Re-query everything except locations pinned by a manual override.
        to_fetch = [loc for loc in dict.fromkeys(locations) if loc and loc not in overrides]
    else:
        # A location that previously failed to geocode (cached as None) is
        # retried, not treated as permanently resolved - a Nominatim miss is
        # often transient (rate limiting, a query the anchor/bias improvements
        # above now handle better), and there's little cost to trying again.
        to_fetch = [loc for loc in dict.fromkeys(locations) if loc and not cache.get(loc)]
    total = len(to_fetch)
    if on_progress:
        on_progress(0, total)
    for i, loc in enumerate(to_fetch):
        query = _insert_missing_city_comma(loc)
        anchor = _anchor_for(loc, location_categories.get(loc), anchors)
        anchor_text = _anchor_query_text(anchor)
        if anchor_text:
            query = f"{query}, {anchor_text}"

        geocode_kwargs = {"addressdetails": True}
        bounded_viewbox = _anchor_viewbox(anchor)
        if bounded_viewbox:
            # A dict-form anchor with known coordinates: hard-restrict to
            # that radius rather than just biasing, so a same-named
            # building/room elsewhere in the world can't win out over the
            # actual campus/workplace location.
            geocode_kwargs.update(viewbox=bounded_viewbox, bounded=True)
        else:
            viewbox = _viewbox_from_points(resolved_points) if len(resolved_points) >= 5 else None
            if viewbox:
                geocode_kwargs.update(viewbox=viewbox, bounded=False)
        try:
            result = geolocator.geocode(query, **geocode_kwargs)
            failure_reason = None if result else "Nominatim found no match for this query"
        except GeocoderTimedOut:
            # geopy's GeocoderTimedOut is a GeocoderServiceError subclass,
            # so it has to be caught first to tell it apart from a real
            # service error below.
            result = None
            failure_reason = f"timed out after {timeout:g}s"
        except GeocoderServiceError as e:
            # Most often a rate limit or a temporary block from Nominatim's
            # public instance - str(e) usually names the HTTP status.
            result = None
            failure_reason = f"Nominatim service error: {e}"
        except Exception as e:
            # A network hiccup or anything else geopy didn't wrap in
            # GeocoderServiceError (e.g. a raw connection error) shouldn't
            # abort the whole batch - previously it did, which could leave
            # a large run stuck partway through with no obvious
            # explanation. This location is just retried like any other
            # failure on the next run.
            result = None
            failure_reason = f"network error: {e}"

        anchor_lat, anchor_lon = (anchor.get("lat"), anchor.get("lon")) if isinstance(anchor, dict) else (None, None)
        has_anchor_coords = isinstance(anchor_lat, (int, float)) and isinstance(anchor_lon, (int, float))

        if result:
            # Structured address fields (felinni.regions' city/country
            # grouping) rather than parsing the free-text display_name,
            # which reads inconsistently depending on what's near the venue.
            address = (result.raw or {}).get("address", {}) if hasattr(result, "raw") else {}
            city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or address.get("county")
            neighbourhood = address.get("suburb") or address.get("neighbourhood") or address.get("quarter") or address.get("city_district") or address.get("borough")
            cache[loc] = {
                "lat": result.latitude, "lon": result.longitude, "display_name": result.address,
                "city": city, "country": address.get("country"), "neighbourhood": neighbourhood,
            }
            resolved_points.append((result.latitude, result.longitude))
            diagnostics.pop(loc, None)
            approximations.pop(loc, None)
        elif has_anchor_coords:
            # Couldn't resolve the specific address even with the anchor's
            # context appended, but the anchor itself has known coordinates
            # (a campus/workplace, not just a query-text nudge) - plot this
            # at the anchor's own location rather than leaving it off the
            # map entirely. Not a "failure" in the diagnostics sense: there
            # IS a usable pin now, just an approximate one.
            anchor_label = anchor.get("query") or loc
            cache[loc] = {
                "lat": anchor_lat, "lon": anchor_lon,
                "display_name": f"{anchor_label} (approximate - couldn't resolve the exact address)",
                "city": None, "country": None, "neighbourhood": None,
            }
            resolved_points.append((anchor_lat, anchor_lon))
            diagnostics.pop(loc, None)
            approximations[loc] = anchor_label
        else:
            cache[loc] = None
            diagnostics[loc] = f'{failure_reason} (query: "{query}")' if query != loc else failure_reason
            approximations.pop(loc, None)

        _save_cache(cache_path, cache)
        _save_json(diagnostics_path, diagnostics)
        _save_json(approximations_path, approximations)
        if on_progress:
            on_progress(i + 1, total)
        time.sleep(rate_limit_seconds)

    return {loc: cache.get(loc) for loc in locations}
