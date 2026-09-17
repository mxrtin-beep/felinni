"""Location-based analysis: place frequency, "radius of life", and places
you used to go and stopped.

Works on the raw `location` string by default (no geocoding needed) since
a stable venue string like "Equinox - Union Square" is already a usable
cluster key. Pass `coords` (from felinni.geocode.geocode_locations) to get
haversine-distance-based radius-of-life and lat/lon for mapping.
"""
from __future__ import annotations

import math

import pandas as pd

EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def place_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """Visit count, first/last seen, and years active per distinct location."""
    located = df.dropna(subset=["location"])
    located = located[located["location"].str.strip() != ""]
    grouped = located.groupby("location").agg(
        visits=("id", "count"),
        first_seen=("start", "min"),
        last_seen=("start", "max"),
        total_hours=("duration_hours", "sum"),
        categories=("category", lambda s: sorted(s.unique())),
    )
    return grouped.sort_values("visits", ascending=False)


def places_you_stopped_going_to(
    df: pd.DataFrame,
    min_visits: int = 3,
    inactive_months: int = 9,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Places visited at least `min_visits` times but not seen in the last
    `inactive_months` months, sorted by how long ago they went quiet."""
    as_of = as_of or df["start"].max()
    freq = place_frequency(df)
    cutoff = as_of - pd.DateOffset(months=inactive_months)
    stopped = freq[(freq["visits"] >= min_visits) & (freq["last_seen"] < cutoff)]
    stopped = stopped.assign(months_since_last_visit=lambda d: (as_of - d["last_seen"]).dt.days / 30.44)
    return stopped.sort_values("last_seen")


def radius_of_life_by_year(
    df: pd.DataFrame,
    coords: dict[str, dict | None],
    home: tuple[float, float] | None = None,
) -> pd.DataFrame:
    """Per-year median and max distance (km) from `home` across located events.

    If `home` isn't given, it's inferred as the centroid of the single
    most-visited location that year (a reasonable proxy for "where you
    lived" when it isn't tagged explicitly).
    """
    located = df.copy()
    located["lat"] = located["location"].map(lambda loc: (coords.get(loc) or {}).get("lat"))
    located["lon"] = located["location"].map(lambda loc: (coords.get(loc) or {}).get("lon"))
    located = located.dropna(subset=["lat", "lon"])

    rows = []
    for year, group in located.groupby("year"):
        if home is not None:
            home_lat, home_lon = home
        else:
            top_location = group["location"].value_counts().idxmax()
            top_row = group[group["location"] == top_location].iloc[0]
            home_lat, home_lon = top_row["lat"], top_row["lon"]

        distances = group.apply(lambda r: _haversine_km(home_lat, home_lon, r["lat"], r["lon"]), axis=1)
        rows.append({
            "year": year,
            "n_located_events": len(group),
            "median_km_from_home": distances.median(),
            "p90_km_from_home": distances.quantile(0.9),
            "max_km_from_home": distances.max(),
        })
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def neighborhood_clusters(
    df: pd.DataFrame,
    coords: dict[str, dict | None],
    grid_km: float = 1.0,
) -> pd.DataFrame:
    """Cluster locations into coarse neighborhood buckets by snapping lat/lon
    to a `grid_km`-sized grid. A cheap stand-in for real reverse-geocoded
    neighborhood names when those aren't available."""
    deg_per_km = 1.0 / 111.0
    cell = grid_km * deg_per_km

    freq = place_frequency(df).reset_index()
    freq["lat"] = freq["location"].map(lambda loc: (coords.get(loc) or {}).get("lat"))
    freq["lon"] = freq["location"].map(lambda loc: (coords.get(loc) or {}).get("lon"))
    freq = freq.dropna(subset=["lat", "lon"])
    freq["cluster"] = (
        (freq["lat"] / cell).round().astype(int).astype(str)
        + ","
        + (freq["lon"] / cell).round().astype(int).astype(str)
    )

    return freq.groupby("cluster").agg(
        locations=("location", list),
        visits=("visits", "sum"),
        center_lat=("lat", "mean"),
        center_lon=("lon", "mean"),
    ).sort_values("visits", ascending=False).reset_index()
