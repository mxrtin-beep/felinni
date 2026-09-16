"""Optional geocoding of the free-text `location` field.

Geocoding is opt-in and network-based (OpenStreetMap Nominatim via geopy),
so it's kept out of the hot path: most analyses (frequency, streaks,
seasonality, people) work on the raw location string alone. Only mapping
and "radius of life" need lat/lon, and those call `geocode_locations`
explicitly.

Results are cached to disk (default `data/geocode_cache.json`) so repeat
runs never re-query the same address, and Nominatim's usage policy (max
~1 request/sec, meaningful User-Agent) is respected.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "geocode_cache.json"

# A building/room name alone ("North Campus Student Center") often won't
# geocode correctly, or geocodes to a same-named place somewhere else in
# the world entirely. When a location's category matches one of these keys
# (case-insensitive substring), the anchor text is appended to the
# GEOCODING QUERY only - never to the stored/displayed location - so
# "North Campus Student Center" searches as "North Campus Student Center,
# UCLA, Los Angeles, CA" instead of drifting worldwide. Edit this to match
# your own campus/workplace calendars.
DEFAULT_LOCATION_ANCHORS = {
    "ucla": "UCLA, Los Angeles, CA",
}


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))


def geocode_locations(
    locations: list[str],
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    user_agent: str = "felinni-calendar-analysis",
    rate_limit_seconds: float = 1.0,
    on_progress=None,
    location_categories: dict[str, str] | None = None,
    anchors: dict[str, str] = DEFAULT_LOCATION_ANCHORS,
) -> dict[str, dict | None]:
    """Geocode a list of unique location strings, returning
    {location: {"lat": ..., "lon": ..., "display_name": ...} or None}.

    Requires the optional `geopy` dependency and network access. Already
    cached locations are never re-queried. If given, `on_progress(done,
    total)` is called once up front with done=0 and again after each
    location, so a CLI or web caller can show progress on what can be a
    multi-minute run (~1 request/sec).

    `location_categories` (location -> category, e.g. from
    `df.groupby("location")["category"].agg(...)`) lets a bare building
    name get an anchor from `anchors` appended to the search query - see
    DEFAULT_LOCATION_ANCHORS.
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
    geolocator = Nominatim(user_agent=user_agent)
    location_categories = location_categories or {}

    to_fetch = [loc for loc in dict.fromkeys(locations) if loc and loc not in cache]
    total = len(to_fetch)
    if on_progress:
        on_progress(0, total)
    for i, loc in enumerate(to_fetch):
        query = loc
        category = location_categories.get(loc)
        if category:
            anchor = next((text for key, text in anchors.items() if key.lower() in category.lower()), None)
            if anchor:
                query = f"{loc}, {anchor}"
        try:
            result = geolocator.geocode(query)
        except GeocoderServiceError:
            result = None
        cache[loc] = (
            {"lat": result.latitude, "lon": result.longitude, "display_name": result.address}
            if result
            else None
        )
        _save_cache(cache_path, cache)
        if on_progress:
            on_progress(i + 1, total)
        time.sleep(rate_limit_seconds)

    return {loc: cache.get(loc) for loc in locations}
