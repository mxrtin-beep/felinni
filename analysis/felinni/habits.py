"""Retroactive habit tracking: consistency, streaks/drop-offs for a
recurring category (e.g. "Gym"), and correlation with how busy other
weeks were."""
from __future__ import annotations

import pandas as pd


def weekly_habit_counts(df: pd.DataFrame, category: str) -> pd.Series:
    """Number of `category` events per ISO week, reindexed to include weeks
    with zero events (gaps matter as much as visits for streak detection)."""
    matches = df[df["category"].str.casefold() == category.casefold()]
    if matches.empty:
        return pd.Series(dtype=int)

    weekly = matches.set_index("start").resample("W").size()
    full_range = pd.date_range(weekly.index.min(), weekly.index.max(), freq="W")
    return weekly.reindex(full_range, fill_value=0)


def streaks_and_gaps(weekly_counts: pd.Series) -> pd.DataFrame:
    """Runs of consecutive active (>0) or inactive (==0) weeks, longest first."""
    if weekly_counts.empty:
        return pd.DataFrame(columns=["start", "end", "weeks", "active"])

    active = weekly_counts > 0
    change = active.ne(active.shift()).cumsum()
    runs = []
    for _, group in active.groupby(change):
        runs.append({
            "start": group.index.min(),
            "end": group.index.max(),
            "weeks": len(group),
            "active": bool(group.iloc[0]),
        })
    return pd.DataFrame(runs).sort_values("weeks", ascending=False).reset_index(drop=True)


def consistency_by_year(weekly_counts: pd.Series) -> pd.DataFrame:
    """Fraction of weeks in each year with at least one habit event."""
    if weekly_counts.empty:
        return pd.DataFrame(columns=["year", "active_weeks", "total_weeks", "consistency"])

    df = weekly_counts.rename("count").to_frame()
    df["year"] = df.index.year
    grouped = df.groupby("year")["count"].agg(
        active_weeks=lambda s: (s > 0).sum(),
        total_weeks="count",
    )
    grouped["consistency"] = grouped["active_weeks"] / grouped["total_weeks"]
    return grouped.reset_index()


def busy_week_hours(df: pd.DataFrame, exclude_category: str | None = None) -> pd.Series:
    """Total scheduled hours per ISO week, optionally excluding the habit's
    own category so it isn't correlated against itself."""
    events = df if exclude_category is None else df[df["category"].str.casefold() != exclude_category.casefold()]
    weekly = events.set_index("start")["duration_hours"].resample("W").sum()
    return weekly


def habit_vs_busyness_correlation(df: pd.DataFrame, category: str) -> dict:
    """Pearson correlation between weekly habit-event counts and how many
    hours the rest of the calendar was booked that week — a negative value
    suggests the habit drops off during busy weeks."""
    habit = weekly_habit_counts(df, category)
    busy = busy_week_hours(df, exclude_category=category)

    aligned = pd.concat([habit.rename("habit_count"), busy.rename("busy_hours")], axis=1).dropna()
    if len(aligned) < 3:
        return {"n_weeks": len(aligned), "correlation": None}

    return {
        "n_weeks": len(aligned),
        "correlation": aligned["habit_count"].corr(aligned["busy_hours"]),
    }
