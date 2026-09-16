"""Spending proxy: estimate how much time (and, via a per-category cost
assumption you supply, money) goes to each activity type.

This never invents dollar amounts — it multiplies your own per-visit cost
estimates (a plain dict you edit) against observed event counts, so the
numbers are only as good as the assumptions you put in.
"""
from __future__ import annotations

import pandas as pd

# Edit these to match your own typical per-visit spend by category.
# Categories not listed here are counted for time but excluded from the
# dollar estimate.
DEFAULT_COST_PER_VISIT = {
    "restaurant": 45,
    "bar": 25,
    "coffee": 8,
    "gym": 0,  # sunk cost of a membership, not per-visit
}


def time_by_category(df: pd.DataFrame) -> pd.DataFrame:
    # All-day events (vacations, birthdays, "out of office" blocks) don't
    # carry a real duration - counting them would blow up "hours" per event.
    df = df[~df["is_all_day"]]
    return df.groupby("category").agg(
        events=("id", "count"),
        total_hours=("duration_hours", "sum"),
    ).assign(
        share_of_hours=lambda d: d["total_hours"] / d["total_hours"].sum()
    ).sort_values("total_hours", ascending=False)


def estimated_spend_by_category(
    df: pd.DataFrame,
    cost_per_visit: dict[str, float] = DEFAULT_COST_PER_VISIT,
) -> pd.DataFrame:
    """Estimated total spend = events * cost_per_visit, for categories with
    a cost assumption supplied. Everything else is reported with spend=NaN
    rather than silently zero, so it's clear what wasn't estimated."""
    time_df = time_by_category(df).reset_index()
    cost_lookup = {k.casefold(): v for k, v in cost_per_visit.items()}
    time_df["cost_per_visit"] = time_df["category"].str.casefold().map(cost_lookup)
    time_df["estimated_spend"] = time_df["events"] * time_df["cost_per_visit"]
    return time_df.sort_values("estimated_spend", ascending=False, na_position="last")


def spend_by_year(
    df: pd.DataFrame,
    cost_per_visit: dict[str, float] = DEFAULT_COST_PER_VISIT,
) -> pd.DataFrame:
    cost_lookup = {k.casefold(): v for k, v in cost_per_visit.items()}
    priced = df[~df["is_all_day"]].copy()
    priced["cost_per_visit"] = priced["category"].str.casefold().map(cost_lookup)
    priced = priced.dropna(subset=["cost_per_visit"])
    return priced.groupby(["year", "category"])["cost_per_visit"].sum().unstack(fill_value=0)
