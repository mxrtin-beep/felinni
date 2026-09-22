"""Groups geocoded locations into geographic metro areas so the Travel tab
can be organized around where in the world you actually were, rather than
requiring every trip to be tagged with a Travel category. Needs locations
already geocoded (felinni.geocode / the dashboard's Geocode button) -
without that, there's nothing here to group by.

Per-city grouping (keyed off Nominatim's `city` field) is too fine for a
top-level view of a real metro area - "Los Angeles", "Santa Monica",
"Pasadena", and "Culver City" are all one trip-worth of geography, not four
separate regions. So locations within METRO_AREA_RADIUS_KM of each other
are clustered into one metro (labeled by whichever city name covers the
most distinct locations in the cluster, not just whichever single address
you visit most often - see `_location_metro_map`), and
`neighborhoods_for_metro` gives a finer breakdown within any one metro for
drilling into your home area or another frequently-visited one.
"""
from __future__ import annotations

import re
from math import atan2, cos, radians, sin, sqrt

import pandas as pd

_NUMERIC_HEAVY = re.compile(r"\d")

# Locations within this radius of each other are treated as one metro area
# (a rough "you'd call this the same trip" distance - covers a metro's
# usual sprawl, e.g. LA into the Valley/Orange County, without merging
# distinct nearby cities like LA and San Diego).
METRO_AREA_RADIUS_KM = 80.0

# Finer clustering radius used only as a fallback within `neighborhoods_for_metro`,
# for locations whose geocoding didn't capture a suburb/neighbourhood name.
NEIGHBORHOOD_RADIUS_KM = 15.0

# Optional renames for a metro's auto-generated label (its most-visited
# city + country) to something more natural, e.g.:
#   METRO_AREA_NAMES = {
#       "San Francisco, United States": "Bay Area",
#       "Toronto, Canada": "Greater Toronto",
#   }
# Edit this to taste - anything not listed keeps its "City, Country" label.
METRO_AREA_NAMES: dict[str, str] = {}


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    lat1, lon1 = p1
    lat2, lon2 = p2
    r = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _cluster_locations(points: dict[str, tuple[float, float]], radius_km: float) -> dict[str, int]:
    """Greedy distance-based clustering: each not-yet-clustered point seeds
    a new cluster and absorbs every remaining point within `radius_km` of
    it. Good enough at personal-calendar scale (dozens to low hundreds of
    distinct locations) without needing a real clustering library."""
    cluster_of: dict[str, int] = {}
    items = list(points.items())
    for loc, point in items:
        if loc in cluster_of:
            continue
        cid = max(cluster_of.values(), default=-1) + 1
        cluster_of[loc] = cid
        for other_loc, other_point in items:
            if other_loc in cluster_of:
                continue
            if _haversine_km(point, other_point) <= radius_km:
                cluster_of[other_loc] = cid
    return cluster_of


def _region_from_display_name(display_name: str | None) -> str | None:
    """Fallback for cache entries geocoded before city/country were
    captured directly (see below): a best-effort "City, Country" guess
    from Nominatim's free-text display_name, e.g. "Tartine Bakery, 600
    Guerrero St, San Francisco, San Francisco County, California, 94110,
    United States" -> "San Francisco, United States". Skips a leading
    venue-name segment (assumed whenever there are enough parts that one
    can be spared) and numeric-heavy segments (house numbers, zips)."""
    if not display_name:
        return None
    parts = [p.strip() for p in display_name.split(",") if p.strip()]
    if len(parts) < 2:
        return display_name
    country = parts[-1]
    candidates = parts[1:-1] if len(parts) > 3 else parts[:-1]
    city = next((p for p in candidates if not _NUMERIC_HEAVY.search(p)), parts[-2])
    return f"{city}, {country}" if city != country else country


def region_for(entry: dict | None) -> str | None:
    """A "City, Country" label for one geocoded cache entry. Uses the
    structured `city`/`country` fields geocode_locations captures directly
    from Nominatim (reliable regardless of how the address reads), falling
    back to parsing `display_name` for entries geocoded before that was
    added. This is the per-city label a metro is ultimately named after -
    see `_location_metro_map` for the actual (coarser) grouping used by the
    Travel tab."""
    if not entry:
        return None
    city, country = entry.get("city"), entry.get("country")
    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    return _region_from_display_name(entry.get("display_name"))


def _location_metro_map(df: pd.DataFrame, coords: dict[str, dict | None]) -> dict[str, str]:
    """location -> metro label, for every geocoded location that appears in
    `df`. Locations within METRO_AREA_RADIUS_KM of each other are one
    metro, labeled after whichever city name is most representative of the
    cluster (with an optional friendly rename from METRO_AREA_NAMES) - see
    below for what "most representative" means and why.
    """
    located = df.dropna(subset=["location"])
    if located.empty:
        return {}
    visit_counts = located["location"].value_counts()

    points = {}
    for loc in visit_counts.index:
        entry = coords.get(loc)
        if entry and isinstance(entry.get("lat"), (int, float)) and isinstance(entry.get("lon"), (int, float)):
            points[loc] = (entry["lat"], entry["lon"])
    if not points:
        return {}

    cluster_of = _cluster_locations(points, METRO_AREA_RADIUS_KM)

    # Picking the label from whichever single LOCATION has the most visits
    # (the previous approach) means one recurring event at one specific
    # venue in a smaller suburb - one address, visited weekly - outweighs a
    # dozen different one-off addresses in the actual well-known regional
    # hub, each visited once or twice: "National Harbor" instead of
    # "Washington, DC", "Goleta" instead of "Santa Barbara" (both observed
    # directly). What a metro's popular name is actually tracking is how
    # much of your life happens somewhere recognizable as that city, not
    # how often you return to any one single address - so this counts
    # DISTINCT locations per city-level label instead, and only falls back
    # to total visits to break a tie between two city labels with the same
    # count of distinct places.
    city_stats: dict[int, dict[str, list[int]]] = {}
    for loc, cid in cluster_of.items():
        raw_label = region_for(coords.get(loc)) or loc
        stats = city_stats.setdefault(cid, {}).setdefault(raw_label, [0, 0])
        stats[0] += 1  # distinct locations under this city label
        stats[1] += int(visit_counts.get(loc, 0))  # their combined visits

    cluster_label = {}
    for cid, labels in city_stats.items():
        best_label = max(labels.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
        cluster_label[cid] = METRO_AREA_NAMES.get(best_label, best_label)

    return {loc: cluster_label[cid] for loc, cid in cluster_of.items()}


def _with_region(df: pd.DataFrame, coords: dict[str, dict | None]) -> pd.DataFrame:
    located = df.dropna(subset=["location"]).copy()
    metro_map = _location_metro_map(df, coords)
    located["region"] = located["location"].map(metro_map.get)
    return located.dropna(subset=["region"])


def visits_by_region(df: pd.DataFrame, coords: dict[str, dict | None]) -> pd.DataFrame:
    """Every distinct metro area you have geocoded activity in - including
    home - with visit counts and date range, most-visited first."""
    with_region = _with_region(df, coords)
    if with_region.empty:
        return pd.DataFrame(columns=["region", "visits", "total_hours", "first_seen", "last_seen", "n_locations"])
    return with_region.groupby("region").agg(
        visits=("id", "count"),
        total_hours=("duration_hours", "sum"),
        first_seen=("start", "min"),
        last_seen=("start", "max"),
        n_locations=("location", "nunique"),
    ).sort_values("visits", ascending=False)


def home_region(region_visits: pd.DataFrame) -> str | None:
    """The metro area with the most visits - a stand-in for "home", the
    same way the Map tab's default view zooms to your densest cluster."""
    return region_visits.index[0] if not region_visits.empty else None


def trips_away_from_home(df: pd.DataFrame, coords: dict[str, dict | None], gap_days: int = 5) -> pd.DataFrame:
    """Collapses consecutive-in-time events in the same non-home metro area
    into distinct trips, the same idea as felinni.travel.trip_timeline but
    keyed by inferred geography instead of a Travel category tag - so it
    picks up every trip whether or not you remembered to tag it."""
    with_region = _with_region(df, coords).sort_values("start")
    if with_region.empty:
        return pd.DataFrame(columns=["region", "start", "end", "n_events", "duration_days"])

    home = with_region["region"].value_counts().idxmax()
    away = with_region[with_region["region"] != home]
    if away.empty:
        return pd.DataFrame(columns=["region", "start", "end", "n_events", "duration_days"])

    trips = []
    current = None
    for _, row in away.iterrows():
        if current and row["region"] == current["region"] and (row["start"] - current["end"]) <= pd.Timedelta(days=gap_days):
            current["end"] = max(current["end"], row["end"])
            current["n_events"] += 1
        else:
            if current:
                trips.append(current)
            current = {"region": row["region"], "start": row["start"], "end": row["end"], "n_events": 1}
    if current:
        trips.append(current)

    result = pd.DataFrame(trips)
    result["duration_days"] = (result["end"] - result["start"]).dt.total_seconds() / 86400.0
    return result.sort_values("start").reset_index(drop=True)


def _neighborhood_label(location: str, coords: dict[str, dict | None]) -> str:
    entry = coords.get(location) or {}
    return entry.get("neighbourhood") or entry.get("city") or location


def neighborhoods_for_metro(
    df: pd.DataFrame,
    coords: dict[str, dict | None],
    metro: str,
) -> pd.DataFrame:
    """Finer breakdown of visits within one metro area - e.g. "LA" split
    into "West LA", "Downtown", "The Valley", "Orange County" - for
    drilling into your home metro or any other popular one instead of
    stopping at the coarse metro-area view. Uses Nominatim's
    suburb/neighbourhood name when geocoding captured one, falling back to
    the location's city (still finer than the whole metro) or its raw
    location string."""
    located = df.dropna(subset=["location"]).copy()
    if located.empty:
        return pd.DataFrame(columns=["neighborhood", "visits", "total_hours", "n_locations"])

    metro_map = _location_metro_map(df, coords)
    located["metro"] = located["location"].map(metro_map.get)
    in_metro = located[located["metro"] == metro]
    if in_metro.empty:
        return pd.DataFrame(columns=["neighborhood", "visits", "total_hours", "n_locations"])

    in_metro = in_metro.copy()
    in_metro["neighborhood"] = in_metro["location"].map(lambda loc: _neighborhood_label(loc, coords))
    return in_metro.groupby("neighborhood").agg(
        visits=("id", "count"),
        total_hours=("duration_hours", "sum"),
        n_locations=("location", "nunique"),
    ).sort_values("visits", ascending=False)
