"""Recurring event tracker: for each named recurring series (a real
Calendar repeat rule - `is_recurring`, not just a shared category), infer
its usual cadence from its own history and flag whether it's falling off
relative to that cadence. Complements felinni.habits (which tracks a
category you pick) by auto-detecting every named commitment - "Book
Club", "Poker Night", ... - without you having to name it.
"""
from __future__ import annotations

import pandas as pd

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
    history to infer a cadence from.

    Defaults `as_of` to the real current time, not the dataset's latest
    event - a calendar export routinely contains events dated after
    today (a recurring series' own future-materialized instances, a
    one-off event you already scheduled), and using whichever happens to
    be latest as "now" can make a series that's still going on look
    "stopped" just because some *other*, unrelated event on your calendar
    happens to be dated later still.
    """
    as_of = as_of or pd.Timestamp.now()
    recurring = df[df["is_recurring"]]

    rows = []
    for title, group in recurring.groupby("title"):
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
