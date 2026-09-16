"""Social pattern analysis: how often you see specific people, whether
time with them is growing or fading, and how social time splits across
people. Requires events tagged with attendees or a `People:`/`With:` line
in notes (see felinni.ingest)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _exploded_people(df: pd.DataFrame) -> pd.DataFrame:
    with_people = df[df["n_people"] > 0].copy()
    return with_people.explode("people").rename(columns={"people": "person"})


def person_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """Total events, hours, and date range spent with each person."""
    exploded = _exploded_people(df)
    return exploded.groupby("person").agg(
        events=("id", "count"),
        total_hours=("duration_hours", "sum"),
        first_seen=("start", "min"),
        last_seen=("start", "max"),
    ).sort_values("events", ascending=False)


def person_trend_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Events per person per year — a pivot table for spotting who you're
    seeing more or less of over time."""
    exploded = _exploded_people(df)
    return (
        exploded.groupby(["person", "year"]).size()
        .unstack(fill_value=0)
        .sort_index()
    )


def person_trend_by_period(df: pd.DataFrame, granularity: str = "year") -> pd.DataFrame:
    """Events per person per period (year/month/week) — index = period start
    timestamp, columns = person. Used for the dashboard's configurable
    "events over time" chart; `person_trend_by_year` above is kept as-is
    since `fading_or_growing`'s year-over-year slope depends on its exact
    (person x calendar-year) shape.

    Buckets via numpy datetime64 truncation rather than `.resample()`,
    since resample's year/month-end aliases ("YE"/"ME") only exist from
    pandas 2.2 - truncating manually works identically on any pandas 2.x.
    """
    exploded = _exploded_people(df)
    if exploded.empty:
        return pd.DataFrame()

    starts = exploded["start"]
    if granularity == "week":
        # Sunday-ending weeks, matching felinni.habits/.anomalies' resample("W").
        days_until_sunday = (6 - starts.dt.weekday) % 7
        period_start = (starts + pd.to_timedelta(days_until_sunday, unit="D")).dt.normalize()
    elif granularity == "month":
        period_start = pd.to_datetime(starts.values.astype("datetime64[M]"))
    else:
        period_start = pd.to_datetime(starts.values.astype("datetime64[Y]"))

    grouped = exploded.assign(period=period_start).groupby(["person", "period"]).size()
    return grouped.unstack(level=0).fillna(0).astype(int)


def fading_or_growing(df: pd.DataFrame, min_total_events: int = 5) -> pd.DataFrame:
    """Fit a simple linear trend (events/year, via least squares on yearly
    counts) per person to flag relationships that are growing vs fading."""
    by_year = person_trend_by_year(df)
    by_year = by_year[by_year.sum(axis=1) >= min_total_events]

    rows = []
    years = np.array(by_year.columns, dtype=float)
    for person, counts in by_year.iterrows():
        counts = counts.to_numpy(dtype=float)
        slope, intercept = np.polyfit(years, counts, 1)
        rows.append({
            "person": person,
            "total_events": int(counts.sum()),
            "slope_events_per_year": slope,
            "first_year": int(years.min()),
            "last_year": int(years.max()),
        })
    return pd.DataFrame(rows).sort_values("slope_events_per_year")


def social_time_share(df: pd.DataFrame) -> pd.DataFrame:
    """Fraction of total tracked social hours spent with each person."""
    freq = person_frequency(df)
    total_hours = freq["total_hours"].sum()
    return freq.assign(share_of_social_hours=freq["total_hours"] / total_hours) if total_hours else freq
