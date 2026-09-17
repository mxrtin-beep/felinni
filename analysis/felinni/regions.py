"""Groups geocoded locations into geographic regions/metro areas (parsed
from Nominatim's display_name) so the Travel tab can be organized around
where in the world you actually were, rather than requiring every trip to
be tagged with a Travel category. Needs locations already geocoded
(felinni.geocode / the dashboard's Geocode button) - without that, there's
nothing here to group by.
"""
from __future__ import annotations

import re

import pandas as pd

_NUMERIC_HEAVY = re.compile(r"\d")


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
    """A "City, Country" region label for one geocoded cache entry. Uses
    the structured `city`/`country` fields geocode_locations captures
    directly from Nominatim (reliable regardless of how the address reads),
    falling back to parsing `display_name` for entries geocoded before
    that was added."""
    if not entry:
        return None
    city, country = entry.get("city"), entry.get("country")
    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    return _region_from_display_name(entry.get("display_name"))


def _with_region(df: pd.DataFrame, coords: dict[str, dict | None]) -> pd.DataFrame:
    located = df.dropna(subset=["location"]).copy()
    located["region"] = located["location"].map(lambda loc: region_for(coords.get(loc)))
    return located.dropna(subset=["region"])


def visits_by_region(df: pd.DataFrame, coords: dict[str, dict | None]) -> pd.DataFrame:
    """Every distinct region you have geocoded activity in - including
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
    """The region with the most visits - a stand-in for "home", the same
    way the Map tab's default view zooms to your densest cluster."""
    return region_visits.index[0] if not region_visits.empty else None


def trips_away_from_home(df: pd.DataFrame, coords: dict[str, dict | None], gap_days: int = 5) -> pd.DataFrame:
    """Collapses consecutive-in-time events in the same non-home region
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
