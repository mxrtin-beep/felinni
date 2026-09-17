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
"""
from __future__ import annotations

import json
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

# A building/room name alone ("North Campus Student Center") often won't
# geocode correctly, or geocodes to a same-named place somewhere else in
# the world entirely. When a location's category OR the location text
# itself matches one of these keys (case-insensitive substring), the
# anchor text is appended to the GEOCODING QUERY only - never to the
# stored/displayed location - so "North Campus Student Center" searches as
# "North Campus Student Center, UCLA, Los Angeles, CA" instead of drifting
# worldwide. Edit this to match your own campus/workplace calendars.
DEFAULT_LOCATION_ANCHORS = {
    "ucla": "UCLA, Los Angeles, CA",
    "amgen": "Amgen, Thousand Oaks, CA",
}


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_cache(cache_path: Path) -> dict:
    return _load_json(cache_path)


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))


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


def geocode_one(query: str, user_agent: str = "felinni-calendar-analysis") -> dict | None:
    """One-off live geocode of a free-text address/place, e.g. a corrected
    location a user typed into the Map tab. Not cached or rate-limited -
    meant for a single interactive lookup, not a bulk run."""
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderServiceError
    except ImportError as e:
        raise ImportError("geocode_one requires geopy: pip install geopy") from e

    geolocator = Nominatim(user_agent=user_agent)
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


def _anchor_for(loc: str, category: str | None, anchors: dict[str, str]) -> str | None:
    haystacks = [loc.lower()] + ([category.lower()] if category else [])
    for key, text in anchors.items():
        if any(key.lower() in h for h in haystacks):
            return text
    return None


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
    user_agent: str = "felinni-calendar-analysis",
    rate_limit_seconds: float = 1.0,
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

    `location_categories` (location -> category, e.g. from
    `df.groupby("location")["category"].agg(...)`) lets a bare building
    name get an anchor from `anchors` appended to the search query - see
    DEFAULT_LOCATION_ANCHORS. Independently, once a handful of locations
    in this run have resolved, later ambiguous queries are biased (not
    restricted) toward the region those already cover.
    """
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderServiceError
    except ImportError as e:
        raise ImportError(
            "geocode_locations requires geopy: pip install geopy"
        ) from e

    cache_path = Path(cache_path)
    cache = _load_cache(cache_path)
    overrides = _load_json(Path(overrides_path))
    geolocator = Nominatim(user_agent=user_agent)
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
        query = loc
        anchor = _anchor_for(loc, location_categories.get(loc), anchors)
        if anchor:
            query = f"{loc}, {anchor}"

        viewbox = _viewbox_from_points(resolved_points) if len(resolved_points) >= 5 else None
        geocode_kwargs = {"addressdetails": True}
        if viewbox:
            geocode_kwargs.update(viewbox=viewbox, bounded=False)
        try:
            result = geolocator.geocode(query, **geocode_kwargs)
        except GeocoderServiceError:
            result = None

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
        else:
            cache[loc] = None

        _save_cache(cache_path, cache)
        if on_progress:
            on_progress(i + 1, total)
        time.sleep(rate_limit_seconds)

    return {loc: cache.get(loc) for loc in locations}
