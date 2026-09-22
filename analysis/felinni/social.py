"""Social pattern analysis: how often you see specific people, whether
time with them is growing or fading, and how social time splits across
people. Requires events tagged with attendees or a `People:`/`With:` line
in notes (see felinni.ingest)."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd


def _exploded_people(df: pd.DataFrame) -> pd.DataFrame:
    """All-day entries (a full-day placeholder like a birthday, or a
    multi-day trip logged as one block) don't carry a real "time spent
    together" the way a timed event does - an all-day event's nominal
    24-hour duration would otherwise wildly inflate a person's hours, and
    counting it as one "event" seeing them is misleading too. Excluded
    here (not per-caller) so every social analysis - frequency, hours,
    trends - gets this consistently, the same reasoning already applied
    in felinni.seasonality/.anomalies/.spending. Applies regardless of
    where an event came from (events.json or an imported calendar source),
    since both flow through the same `is_all_day` field."""
    with_people = df[~df["is_all_day"] & (df["n_people"] > 0)].copy()
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
    all_years = np.array(by_year.columns, dtype=float)
    for person, counts in by_year.iterrows():
        counts = counts.to_numpy(dtype=float)
        # Fit only from this person's own first active year onward - the
        # calendar's full year range (all_years) may start well before they
        # ever show up, and those leading zero-years would otherwise get
        # read as part of the trend, making a person who simply entered the
        # picture partway through look "growing" no matter how their own
        # history actually moved. Trailing zero-years (after their last
        # event) are kept, since a recent gap is real fading signal.
        first_idx = np.flatnonzero(counts)[0]
        years, counts = all_years[first_idx:], counts[first_idx:]
        if len(years) < 2:
            continue
        slope, intercept = np.polyfit(years, counts, 1)
        rows.append({
            "person": person,
            "total_events": int(counts.sum()),
            "slope_events_per_year": slope,
            "first_year": int(years[0]),
            "last_year": int(all_years[-1]),
        })
    return pd.DataFrame(rows).sort_values("slope_events_per_year")


def social_time_share(df: pd.DataFrame) -> pd.DataFrame:
    """Fraction of total tracked social hours spent with each person."""
    freq = person_frequency(df)
    total_hours = freq["total_hours"].sum()
    return freq.assign(share_of_social_hours=freq["total_hours"] / total_hours) if total_hours else freq


def friend_network_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Every pair of people who appear together in at least one real,
    timed event - a "you both showed up to this" link, for a friend
    network graph. Only multi-person events count (an event with just
    one attendee has no pair to form), and all-day placeholders are
    excluded for the same reason `_exploded_people` excludes them
    elsewhere - a full-day block isn't "hanging out together" the way a
    timed event is. Columns: person_a, person_b, shared_events (how many
    events they were both tagged in - the edge's weight)."""
    with_people = df[~df["is_all_day"] & (df["n_people"] > 1)]
    pair_counts: dict[tuple[str, str], int] = {}
    for people in with_people["people"]:
        for a, b in itertools.combinations(sorted(set(people)), 2):
            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
    if not pair_counts:
        return pd.DataFrame(columns=["person_a", "person_b", "shared_events"])
    rows = [{"person_a": a, "person_b": b, "shared_events": w} for (a, b), w in pair_counts.items()]
    return pd.DataFrame(rows).sort_values("shared_events", ascending=False).reset_index(drop=True)
