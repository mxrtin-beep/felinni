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

Several query-shape fixups run before any of that, all aimed at the same
thing: a calendar app's "location" field is often a multi-line map address
(name / street / city-state-zip) flattened into one string, and whatever
did the flattening doesn't always leave it in a shape Nominatim's parser
likes.

- Whitespace (including a literal embedded newline where a line break in
  the original address used to be - confirmed directly on one failure
  whose logged request contained a raw `%0A`) is collapsed to single spaces
  before every query.
- A location that already looks like a complete mailing address (has its
  own ZIP, "United States", or "Canada") skips the anchor entirely -
  appending redundant context to an already-complete address doesn't help
  and can make Nominatim's parser fail outright instead of just ignoring
  the extra text.
- A missing comma between the street and city ("...Strathmore Dr Los
  Angeles, CA...") is inserted back, and so is a fully comma-less
  "Street City ST ZIP" ("...Shumway Lane Mountain View CA 94041") -
  Nominatim leans heavily on commas to tell address components apart. A
  directional qualifier right after the street suffix ("...4th St NW...",
  "...Wilshire Blvd W...") is kept with the street rather than the city
  when inserting these commas - left alone, a two-letter compass point
  (NW/NE/SW/SE) reads exactly like a short capitalized city-name word and
  gets absorbed into the city ("St NW Washington" -> city "NW Washington"
  instead of "Washington"), while a single-letter one (N/S/E/W) is too
  short to match that pattern at all and blocks the comma insertion from
  matching anywhere in the string.
- If the (whitespace-normalized, comma-fixed) query for a complete address
  still fails, several retries are tried in order (see `_retry_queries`):
  a business name that's glued directly onto its own house number stripped
  off ("101 Boxing Club 1714 Newbury Rd..." -> "1714 Newbury Rd...") - see
  `_strip_business_name_prefix` for exactly how the real house number is
  picked out (not simply "the first number", since plenty of venue names
  themselves start with a digit: "10 Speed Coffee", "99 Ranch Market");
  a unit/suite/floor/apartment/room clause dropped (`_strip_unit_designator`
  - handles "Unit 1420", "Apt. 309", "Suite #20", and "Unit A&D" alike, and
  re-checks for a business name a second time afterward, since the clause
  can hide the comma that split needed); just the business/landmark name
  plus its city on their own, when the full street address won't resolve at
  all but the two together do ("Balboa Park 1549 El Prado, San Diego..." ->
  "Balboa Park, San Diego" - see `_business_name_and_city`, for landmarks
  Nominatim indexes by name rather than mailing address); and a handful of
  well-known Los Angeles-area neighborhoods used as the mailing "city"
  substituted for "Los Angeles" itself, since Nominatim's place hierarchy
  sometimes only recognizes the containing city ("...Van Nuys, CA..." ->
  "...Los Angeles, CA...", see `_LA_NEIGHBORHOODS`).

None of these are guaranteed - an address with no recognizable tail at all,
a real house number that can't be confidently separated from a suite/unit
number, or a bare unmarked unit ID with no "Unit"/"Apt"/"#" keyword and no
comma at all to signal it's separate ("5800 Bristol Pkwy C3 Culver City,
CA..." - "C3" reads exactly like a second street-number/suite fragment,
too ambiguous to safely strip) still needs a manual override.
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
        "state": address.get("state"),
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


_COMPLETE_ADDRESS_RE = re.compile(r"\b\d{5}(-\d{4})?\b|\bunited states\b|\busa\b|\bcanada\b", re.IGNORECASE)


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


def _normalize_whitespace(text: str) -> str:
    """A calendar app's "location" field often comes from a multi-line map
    address (name / street / city-state-zip on separate lines) flattened
    into one string - and the line breaks don't always survive as visible
    punctuation. A literal embedded newline in a Nominatim query isn't just
    cosmetic: it can badly confuse the parser (observed directly on a
    "Nominatim service error" failure whose logged request URL contained a
    raw `%0A` between the venue name and the street address). Collapsing
    all whitespace - newlines, tabs, doubled spaces - to single spaces
    before querying costs nothing for an already-clean address and can
    turn an unparseable one into a resolvable one."""
    return re.sub(r"\s+", " ", text).strip()


# Calendar apps often hand back a location as "<name> <street address>" with
# the multi-line address (as shown in a map picker) flattened onto one line -
# and the line break between the street and the city sometimes turns into a
# bare space instead of a comma ("...Newbury Park Dr Newbury Park, CA..."
# has one but "...11024 Strathmore Dr Los Angeles, CA..." doesn't), is
# missing entirely ("...Shumway Lane Mountain View CA 94041" has no commas
# at all), or is missing just between the city and state ("...Venice CA
# 90291" - the street->city comma is fine, only "Venice, CA" needs one).
# Nominatim leans heavily on commas to separate address components, so a
# query missing them can fail to resolve even though the address itself is
# perfectly real - inserting them back is usually enough. This assumes the
# English convention of the street type coming last ("Sawtelle Blvd") -
# some Southern California streets use a Spanish-style type-first name
# ("Avenida de los Arboles"), which this comma-insertion doesn't reach
# (the type isn't adjacent to the city), though the type is still
# recognized for other purposes (see `_strip_business_name_prefix`).
_STREET_SUFFIXES = (
    r"St|Street|Ave|Avenue|Avenida|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Way|Ct|Court|"
    r"Pl|Place|Plaza|Cir|Circle|Pkwy|Parkway|Ter|Terrace|Hwy|Highway|Sq|Square|Row|Walk|"
    r"Trail|Path|Loop|Camino|Paseo|Calle|Via"
)
# A directional qualifier after the street suffix ("...4th St NW...",
# "...Wilshire Blvd W...") - without accounting for it, the two-letter
# compass points (NW/NE/SW/SE) are indistinguishable from a short
# capitalized city-name word and get wrongly swallowed into the "city"
# capture below ("St NW Washington" -> city "NW Washington" instead of
# "Washington"), while the single-letter ones (N/S/E/W) are too short to
# match that same city-word pattern and block the whole regex from
# matching at all, leaving no comma inserted anywhere. Grouped with the
# suffix itself (not the city) so the existing \1 (street), \2 (city), \3
# (state) substitutions below don't need to change.
_STREET_SUFFIX_WITH_DIRECTION = rf"(?:{_STREET_SUFFIXES})(?:\s+(?:NE|NW|SE|SW|N|S|E|W)\.?\b)?"
_MISSING_CITY_COMMA_RE = re.compile(
    rf"\b({_STREET_SUFFIX_WITH_DIRECTION})\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*),\s*([A-Z]{{2}})\b"
)
_FULLY_COMMALESS_CITY_STATE_RE = re.compile(
    rf"\b({_STREET_SUFFIX_WITH_DIRECTION})\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*)\s+([A-Z]{{2}})(\s+\d{{5}}(?:-\d{{4}})?)?\b"
)
# A city that already follows a comma (so it's not just any two capitalized
# words) but runs straight into its state code with no comma of its own -
# "...Blvd, Venice CA 90291..." rather than "...Blvd, Venice, CA 90291...".
_MISSING_STATE_COMMA_RE = re.compile(
    r",\s*([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*)\s+([A-Z]{2})\b"
)
# The state/ZIP running straight into the country with no comma of its own -
# "...CA 91320 United States" rather than "...CA 91320, United States". The
# character class excludes a comma so an already-correct "..., United
# States" is left alone rather than getting a second comma stacked on.
_MISSING_COUNTRY_COMMA_RE = re.compile(r"([^\s,])\s+(United States|USA|Canada)\b", re.I)


def _insert_missing_city_comma(text: str) -> str:
    text = _FULLY_COMMALESS_CITY_STATE_RE.sub(
        lambda m: f"{m.group(1)}, {m.group(2)}, {m.group(3)}{m.group(4) or ''}", text
    )
    text = _MISSING_CITY_COMMA_RE.sub(r"\1, \2, \3", text)
    text = _MISSING_STATE_COMMA_RE.sub(r", \1, \2", text)
    return _MISSING_COUNTRY_COMMA_RE.sub(r"\1, \2", text)


# A venue name glued directly onto its own house number with no separating
# punctuation at all ("101 Boxing Club 1714 Newbury Rd, Unit S, Newbury
# Park, CA 91320, United States") reads to Nominatim as one nonsensical
# street name. The real house number is the LAST bare (non-ordinal) number
# in the address before its city/state/zip/country tail that still leads
# into a recognized street suffix - "last", not "first", because plenty of
# venue names themselves start with a digit ("10 Speed Coffee", "101 Boxing
# Club", "99 Ranch Market"); "non-ordinal" excludes a numbered street name
# like "30th St" or "22nd St" (its number is glued directly to a letter
# suffix with no word boundary, so \b\d+\b already skips it); "leads into a
# street suffix" excludes a trailing unit/suite number ("...Unit 9") that
# has nothing recognizable after it.
_KNOWN_ADDRESS_TAIL_RE = re.compile(
    r",\s*[A-Za-z][A-Za-z .]*,\s*[A-Z]{2}\s*(?:\d{5}(?:-\d{4})?)?,\s*(?:United States|USA)\s*$", re.I
)
_BARE_NUMBER_RE = re.compile(r"\b\d+\b")
_ANY_STREET_SUFFIX_RE = re.compile(rf"\b(?:{_STREET_SUFFIXES})\b", re.I)


def _split_business_name_prefix(text: str) -> tuple[str, str] | None:
    """The same split point `_strip_business_name_prefix` uses, but
    returning both halves - (business_name, address_without_it) - instead
    of discarding the business name. None on the same "nothing to split"
    conditions."""
    tail_match = _KNOWN_ADDRESS_TAIL_RE.search(text)
    if not tail_match:
        return None
    head = text[:tail_match.start()]
    candidates = [m.start() for m in _BARE_NUMBER_RE.finditer(head)]
    split_at = next((pos for pos in reversed(candidates) if pos > 0 and _ANY_STREET_SUFFIX_RE.search(head, pos)), None)
    if split_at is None:
        return None
    business_name = text[:split_at].strip()
    address = text[split_at:]
    if not business_name or address == text:
        return None
    return business_name, address


def _strip_business_name_prefix(text: str) -> str | None:
    """For "10 Speed Coffee 1947 Sawtelle Blvd, Unit B, Los Angeles, CA
    90025, United States", returns "1947 Sawtelle Blvd, Unit B, Los
    Angeles, CA 90025, United States" - the real mailing address, with the
    business name that's glued onto its own house number dropped. Returns
    None when there's nothing to strip (no recognized address tail, or the
    rightmost number that leads into a street suffix is already at the
    very start - i.e. the address likely already starts at its real house
    number, nothing before it to drop)."""
    split = _split_business_name_prefix(text)
    return split[1] if split else None


def _business_name_and_city(text: str) -> str | None:
    """For "Balboa Park 1549 El Prado, San Diego, CA 92101, United States",
    returns "Balboa Park, San Diego" - a large landmark (a park, a campus,
    a plaza) is sometimes indexed by Nominatim under its own name rather
    than its literal mailing address, so the full street address fails to
    resolve at all even though "<name>, <city>" finds it directly. Tried
    only as a last resort in `_retry_queries`, since it throws away the
    street address entirely - a coincidentally-matching but wrong place
    with the same name in the same city is the risk, not just a miss.

    Unlike `_strip_business_name_prefix`, the split here doesn't require
    the house number to lead into a *recognized* street suffix - "El
    Prado", "Ocean Front Walk", and other real street names aren't in
    `_STREET_SUFFIXES` at all, but the landmark name still needs to be
    separated from its house number to build "<name>, <city>"."""
    tail_match = _KNOWN_ADDRESS_TAIL_RE.search(text)
    if not tail_match:
        return None
    # A unit/suite/room number ("...Ave, Unit 1420") would otherwise look
    # like the "last bare number" below, wrongly landing the split inside
    # the unit clause instead of at the real house number - drop it first.
    head = re.sub(r"\s+", " ", _UNIT_DESIGNATOR_RE.sub("", text[:tail_match.start()])).strip()
    candidates = [m.start() for m in _BARE_NUMBER_RE.finditer(head)]
    split_at = next((pos for pos in reversed(candidates) if pos > 0), None)
    if split_at is None:
        return None
    business_name = head[:split_at].strip()
    if not business_name:
        return None
    city_match = re.match(r",\s*([A-Za-z][A-Za-z .]*?)\s*,", tail_match.group(0))
    if not city_match:
        return None
    city = city_match.group(1).strip()
    if not city:
        return None
    result = f"{business_name}, {city}"
    return result if result != text else None


# Nominatim's house-number-level data often doesn't carry unit/suite/floor
# detail at all - a query for "3096 McClintock Ave, Unit 1420, Los Angeles,
# CA 90007, United States" can fail to resolve purely because of the "Unit
# 1420" clause, even though "3096 McClintock Ave, Los Angeles, CA 90007,
# United States" resolves fine. Dropping a unit/suite/floor/apartment/room
# segment is a safe, unambiguous edit (it's never the address's own street
# or city, just extra detail Nominatim can't place), unlike guessing where
# a street name starts - so unlike the comma-delimited case, a leading
# comma isn't required to match: "19401 Parthenia St Apt 1076 Northridge,
# CA, United States" has no comma anywhere near "Apt 1076" either.
# Two subtleties fixed here that broke real addresses:
# - `\.?\b` (optional period, THEN a word boundary) never matches when the
#   period IS present, because a period and the space after it are both
#   non-word characters - there's no "boundary" between two non-word
#   characters for \b to find. "Apt. 309" needs the boundary checked right
#   after the bare keyword instead: `\b\.?`.
# - The trailing unit ID used `[\w-]+`, which stops at the first character
#   outside \w/hyphen - so "Suite #20" only matched "#20" (leaving "Suite"
#   behind as orphaned text) and "Unit A&D" only matched "Unit A" (leaving
#   "&D" glued onto the street name). The keyword and an optional "#" are
#   now matched together, and the ID itself allows "&" and "/" too (unit
#   IDs like "A&D" or "3/4" both show up in the wild).
_UNIT_DESIGNATOR_RE = re.compile(
    r",?\s*(?:(?:Unit|Ste|Suite|Apt|Apartment|Bldg|Building|Fl|Floor|Rm|Room)\b\.?\s*#?|#)\s*[\w&/-]+",
    re.I,
)


def _strip_unit_designator(text: str) -> str | None:
    """Drops a unit/suite/floor/apartment/room clause. When it wasn't
    comma-delimited to begin with, removing it can newly expose a
    street->city gap that was previously hidden behind it ("...St Apt 1076
    Northridge..." -> "...St  Northridge..." -> "...St, Northridge...") -
    re-running the comma fixups catches that."""
    stripped = _UNIT_DESIGNATOR_RE.sub("", text, count=1)
    if stripped == text:
        return None
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return _insert_missing_city_comma(stripped)


# A handful of well-known Los Angeles-area neighborhoods that are USPS
# "delivery cities" (the city name that actually appears in the mailing
# address) but aren't their own incorporated municipality - they're part
# of the City of Los Angeles, and Nominatim's place hierarchy sometimes
# only recognizes the containing city, not the neighborhood name, as
# "city" for an address lookup ("7610 Woodley Ave, Van Nuys, CA" fails,
# but "7610 Woodley Ave, Los Angeles, CA" - or just "Van Nuys, CA" as its
# own place - resolves). Not exhaustive; just the ones observed to fail.
_LA_NEIGHBORHOODS = {
    "van nuys", "pacific palisades", "woodland hills", "north hollywood",
    "sherman oaks", "studio city", "encino", "tarzana", "reseda",
    "canoga park", "winnetka", "west hills", "chatsworth", "northridge",
    "granada hills", "porter ranch", "sylmar", "sun valley", "panorama city",
    "mission hills", "arleta", "lake balboa", "valley glen", "toluca lake",
    "eagle rock", "highland park", "mount washington", "atwater village",
    "silver lake", "echo park", "los feliz", "hollywood", "west adams",
    "mar vista", "playa vista", "playa del rey", "westchester", "venice",
    "brentwood", "bel air", "westwood", "san pedro", "wilmington",
    "harbor city", "sunland", "tujunga", "shadow hills", "west hollywood",
}
_LA_NEIGHBORHOOD_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(_LA_NEIGHBORHOODS, key=len, reverse=True)) + r")\s*,\s*CA\b",
    re.I,
)


def _substitute_la_neighborhood_city(text: str) -> str | None:
    """Swaps a recognized LA-area neighborhood name used as the "city" for
    "Los Angeles" - see `_LA_NEIGHBORHOODS`. Only ever tried as a last
    resort in `_retry_queries`, after the neighborhood name on its own has
    already failed to resolve."""
    match = _LA_NEIGHBORHOOD_CITY_RE.search(text)
    if not match:
        return None
    replaced = text[:match.start(1)] + "Los Angeles" + text[match.end(1):]
    return replaced if replaced != text else None


def _retry_queries(query: str) -> list[tuple[str, str]]:
    """Extra queries worth trying, in order, when `query` fails outright -
    each paired with a short label for the diagnostics note describing
    what changed. Only ever removes or substitutes text (a business name
    prefix, a unit/suite clause, a neighborhood name for its containing
    city) - never invents anything out of nothing."""
    candidates = []

    business_stripped = _strip_business_name_prefix(query)
    base = business_stripped or query
    if business_stripped:
        candidates.append((business_stripped, "stripping the likely business name"))

    unit_stripped = _strip_unit_designator(base)
    if unit_stripped and unit_stripped != query:
        candidates.append((unit_stripped, "dropping the unit/suite"))
        base = unit_stripped
        # The business name may not have been strippable until the
        # unit/suite clause was gone (it can hide the comma the business-
        # name split relies on) - "Home 6330 Randi Avenue #E208 Woodland
        # Hills..." only splits cleanly as a business name after "#E208"
        # is dropped, since the split point's boundary check needs the
        # already-formed "..., Woodland Hills, CA ..." tail.
        second_pass = _strip_business_name_prefix(unit_stripped)
        if second_pass and second_pass not in (c for c, _ in candidates):
            candidates.append((second_pass, "stripping the likely business name and dropping the unit/suite"))

    name_and_city = _business_name_and_city(query)
    if name_and_city and name_and_city not in (c for c, _ in candidates):
        candidates.append((name_and_city, "trying just the name and city"))

    neighborhood_fixed = _substitute_la_neighborhood_city(base)
    if neighborhood_fixed and neighborhood_fixed != query:
        candidates.append((neighborhood_fixed, "trying the containing city (Los Angeles) instead of the neighborhood"))

    return candidates


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
    def _attempt(query_text: str, **kwargs):
        try:
            attempt_result = geolocator.geocode(query_text, **kwargs)
            return attempt_result, (None if attempt_result else "Nominatim found no match for this query")
        except GeocoderTimedOut:
            # geopy's GeocoderTimedOut is a GeocoderServiceError subclass,
            # so it has to be caught first to tell it apart from a real
            # service error below.
            return None, f"timed out after {timeout:g}s"
        except GeocoderServiceError as e:
            # Most often a rate limit or a temporary block from Nominatim's
            # public instance - str(e) usually names the HTTP status.
            return None, f"Nominatim service error: {e}"
        except Exception as e:
            # A network hiccup or anything else geopy didn't wrap in
            # GeocoderServiceError (e.g. a raw connection error) shouldn't
            # abort the whole batch - previously it did, which could leave
            # a large run stuck partway through with no obvious
            # explanation. This location is just retried like any other
            # failure on the next run.
            return None, f"network error: {e}"

    for i, loc in enumerate(to_fetch):
        query = _insert_missing_city_comma(_normalize_whitespace(loc))
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

        result, failure_reason = _attempt(query, **geocode_kwargs)
        diagnostics_note = f'{failure_reason} (query: "{query}")' if query != loc else failure_reason

        if not result and _looks_like_complete_address(loc):
            # A venue name glued directly onto its own house number
            # ("101 Boxing Club 1714 Newbury Rd...") reads as one
            # nonsensical street to Nominatim, and a unit/suite/floor
            # clause ("...Unit 1420...") often has no match in Nominatim's
            # house-number-level data even when the plain street address
            # resolves fine - retry with each fixable, in order, stopping
            # at the first one that resolves.
            for candidate_query, label in _retry_queries(query):
                time.sleep(rate_limit_seconds)
                retry_result, retry_reason = _attempt(candidate_query, **geocode_kwargs)
                if retry_result:
                    result = retry_result
                    break
                diagnostics_note = f'{diagnostics_note}; also tried {label} ("{candidate_query}"): {retry_reason}'

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
                "state": address.get("state"),
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
            diagnostics[loc] = diagnostics_note
            approximations.pop(loc, None)

        _save_cache(cache_path, cache)
        _save_json(diagnostics_path, diagnostics)
        _save_json(approximations_path, approximations)
        if on_progress:
            on_progress(i + 1, total)
        time.sleep(rate_limit_seconds)

    return {loc: cache.get(loc) for loc in locations}
