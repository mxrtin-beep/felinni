"""Seasonality: how gym/social/dating/etc activity changes by month or
season, for noticing burnout cycles or planning around your own patterns."""
from __future__ import annotations

import pandas as pd

SEASON_ORDER = ["winter", "spring", "summer", "fall"]
MONTH_ORDER = list(range(1, 13))


def monthly_activity(df: pd.DataFrame, category: str | None = None) -> pd.DataFrame:
    """Average events per month-of-year (across all years), optionally
    filtered to one category."""
    events = df if category is None else df[df["category"].str.casefold() == category.casefold()]
    n_years = events["year"].nunique() or 1
    counts = events.groupby("month").size().reindex(MONTH_ORDER, fill_value=0)
    return (counts / n_years).rename("avg_events_per_month").to_frame()


def seasonal_activity(df: pd.DataFrame, category: str | None = None) -> pd.DataFrame:
    events = df if category is None else df[df["category"].str.casefold() == category.casefold()]
    n_years = events["year"].nunique() or 1
    counts = events.groupby("season").size().reindex(SEASON_ORDER, fill_value=0)
    return (counts / n_years).rename("avg_events_per_season").to_frame()


def category_seasonality_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Rows = category, columns = season, values = average events per season
    per year active — good input for a heatmap."""
    years_active = df.groupby("category")["year"].nunique()
    counts = df.groupby(["category", "season"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=SEASON_ORDER, fill_value=0)
    return counts.div(years_active, axis=0)
