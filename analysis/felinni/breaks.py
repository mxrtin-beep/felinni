"""Notable breaks/transitions in your calendar: a category's activity
starting or stopping for a long stretch (a job, a school term, a habit),
extended unusually-quiet stretches (long trips, burnout, a move), and a
rough "did my home base change" signal from which raw location dominates
each quarter. All heuristic - meant to point you at dates worth a second
look, not a definitive timeline.
"""
from __future__ import annotations

import pandas as pd

from felinni import habits
from felinni.anomalies import weekly_load


def category_phases(df: pd.DataFrame, min_weeks: int = 8, min_total_events: int = 8) -> pd.DataFrame:
    """For every category with enough history, its long (>= min_weeks)
    active streaks and gaps - e.g. "Work: active Jan 2021 - May 2023" or
    "Work: break Jun 2023 - Sep 2023" (13 weeks) - a proxy for jobs,
    school terms, or long-running habits starting/stopping."""
    rows = []
    counts = df["category"].value_counts()
    categories = counts[counts >= min_total_events].index.tolist()
    for category in categories:
        weekly = habits.weekly_habit_counts(df, category)
        if weekly.empty:
            continue
        streaks = habits.streaks_and_gaps(weekly)
        for _, s in streaks[streaks["weeks"] >= min_weeks].iterrows():
            rows.append({
                "category": category,
                "type": "active" if s["active"] else "break",
                "start": s["start"],
                "end": s["end"],
                "weeks": int(s["weeks"]),
            })
    if not rows:
        return pd.DataFrame(columns=["category", "type", "start", "end", "weeks"])
    return pd.DataFrame(rows).sort_values("start").reset_index(drop=True)


def quiet_stretches(df: pd.DataFrame, min_weeks: int = 3, z_threshold: float = 1.0) -> pd.DataFrame:
    """Runs of >= min_weeks consecutive unusually-quiet weeks (z <=
    -z_threshold on total scheduled hours) - long trips, burnout dips, a
    move that emptied the calendar for a while."""
    load = weekly_load(df)
    mean, std = load["total_hours"].mean(), load["total_hours"].std()
    if not std:
        return pd.DataFrame(columns=["start", "end", "weeks"])

    quiet = (load["total_hours"] - mean) / std <= -z_threshold
    change = quiet.ne(quiet.shift()).cumsum()
    rows = []
    for _, group in load.groupby(change):
        if quiet.loc[group.index[0]] and len(group) >= min_weeks:
            rows.append({"start": group.index.min(), "end": group.index.max(), "weeks": len(group)})
    return pd.DataFrame(rows)


def location_shifts(df: pd.DataFrame, min_share: float = 0.4) -> pd.DataFrame:
    """Rough home-move detector: the single most frequent raw location
    string per quarter, flagged whenever it changes from the previous
    quarter. Needs a location tagged consistently on routine events (home,
    a nearby gym, ...) to mean anything - no geocoding required.

    A quarter only counts as a signal when its top location holds at least
    `min_share` of that quarter's located events - otherwise it's noise
    (several similarly-frequent places, e.g. rotating between gyms) rather
    than a real change in "home base", and is skipped without disturbing
    the running comparison.

    Quarter boundaries are computed by hand (year/month arithmetic) rather
    than a pandas frequency alias - "Q" was removed in pandas 3.0 in favor
    of "QE", so relying on either breaks on some pandas version.
    """
    located = df.dropna(subset=["location"])
    if located.empty:
        return pd.DataFrame(columns=["quarter", "location", "visits"])

    quarter_month = ((located["start"].dt.month - 1) // 3) * 3 + 1
    quarter = pd.to_datetime({"year": located["start"].dt.year, "month": quarter_month, "day": 1})

    rows = []
    prev_location = None
    for period in sorted(quarter.unique()):
        group = located[quarter.values == period]
        top = group["location"].value_counts()
        top_location = top.index[0]
        if top.iloc[0] / len(group) < min_share:
            continue  # no clear majority this quarter - not a signal either way
        if prev_location is not None and top_location != prev_location:
            rows.append({"quarter": period, "location": top_location, "visits": int(top.iloc[0])})
        prev_location = top_location
    return pd.DataFrame(rows)
