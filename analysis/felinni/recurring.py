"""Recurring event tracker: for each named recurring series, infer its
usual cadence from its own history and flag whether it's falling off
relative to that cadence. Complements felinni.habits (which tracks a
category you pick) by auto-detecting every named commitment - "Book
Club", "Poker Night", ... - without you having to name it.

Detected from the title repeating at least `min_occurrences` times, not
from Calendar's own `is_recurring` repeat-rule flag: a real habit like a
gym rotation ("Push Day", "Pull Day", ...) is often typed in fresh each
time rather than set up as a formal repeat rule, and even a properly
recurring series can pick up an irregular per-occurrence title (e.g. a
"with A, B" guest list attached by hand on some occurrences but not
others) that would make `is_recurring` unreliable as the sole signal
either way. Titles are also normalized by stripping a trailing "with
A, B" guest list (felinni.ingest.strip_with_suffix) before grouping, so
"Dinner with Alice" and "Dinner with Bob" count as the same series
rather than each falling short of the occurrence threshold alone.
"""
from __future__ import annotations

import pandas as pd

from .ingest import strip_with_suffix

STATUS_ORDER = {"stopped": 0, "slowing down": 1, "active": 2}


def recurring_series(
    df: pd.DataFrame,
    min_occurrences: int = 4,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """One row per distinct recurring-event title, with its median interval
    between occurrences and a status:
      - "active": last seen within 1.5x its usual interval
      - "slowing down": within 3x
      - "stopped": longer than that, or no longer occurring at its old pace
    Series with fewer than `min_occurrences` are skipped - too little
    history to infer a cadence from. Capped at "monthly" cadence - a
    "quarterly"/"yearly" series (e.g. an annual trip re-tagged with the
    same title each year) is filtered out entirely, since at that
    interval `min_occurrences` alone takes years of history to satisfy
    and the active/slowing-down/stopped status reads as noise rather
    than a meaningful habit signal.

    Defaults `as_of` to the real current time, not the dataset's latest
    event - a calendar export routinely contains events dated after
    today (a recurring series' own future-materialized instances, a
    one-off event you already scheduled), and using whichever happens to
    be latest as "now" can make a series that's still going on look
    "stopped" just because some *other*, unrelated event on your calendar
    happens to be dated later still.
    """
    as_of = as_of or pd.Timestamp.now()
    normalized = df.assign(_series_title=df["title"].apply(strip_with_suffix))

    rows = []
    for title, group in normalized.groupby("_series_title"):
        dates = group["start"].sort_values()
        if len(dates) < min_occurrences:
            continue
        intervals_days = dates.diff().dropna().dt.total_seconds() / 86400
        median_interval = intervals_days.median()
        if not median_interval or median_interval <= 0:
            continue

        last_seen = dates.max()
        days_since = (as_of - last_seen).total_seconds() / 86400
        ratio = days_since / median_interval

        if ratio <= 1.5:
            status = "active"
        elif ratio <= 3:
            status = "slowing down"
        else:
            status = "stopped"

        first_seen = dates.min()
        rows.append({
            "title": title,
            "category": group["category"].mode().iat[0],
            "occurrences": len(dates),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "streak_days": (last_seen - first_seen).days,
            "median_interval_days": median_interval,
            "days_since_last": days_since,
            "status": status,
        })

    if not rows:
        return pd.DataFrame(columns=[
            "title", "category", "occurrences", "first_seen", "last_seen",
            "streak_days", "median_interval_days", "days_since_last", "status", "cadence",
        ])

    result = pd.DataFrame(rows)
    result["cadence"] = result["median_interval_days"].apply(cadence_label)
    result = result[~result["cadence"].isin(("quarterly", "yearly"))].reset_index(drop=True)
    result["_status_rank"] = result["status"].map(STATUS_ORDER)
    return (
        result.sort_values(["_status_rank", "days_since_last"], ascending=[True, False])
        .drop(columns="_status_rank")
        .reset_index(drop=True)
    )


def cadence_label(median_interval_days: float) -> str:
    """A human label for a median inter-occurrence gap, e.g. "weekly"."""
    if median_interval_days <= 2:
        return "daily"
    if median_interval_days <= 9:
        return "weekly"
    if median_interval_days <= 18:
        return "biweekly"
    if median_interval_days <= 45:
        return "monthly"
    if median_interval_days <= 100:
        return "quarterly"
    return "yearly"
