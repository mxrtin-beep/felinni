"""Anomaly spotting: weeks that were unusually packed or unusually empty,
as a rough proxy for stress or low periods."""
from __future__ import annotations

import numpy as np
import pandas as pd

from felinni.ingest import timed_events


def weekly_load(df: pd.DataFrame) -> pd.DataFrame:
    """Total scheduled hours and event count per ISO week, with gaps
    (weeks with zero events) filled in — those are anomalies too. All-day
    events (vacations, birthdays, holidays) are excluded, since a week
    isn't "packed" or "empty" because of those."""
    df = timed_events(df)
    weekly_hours = df.set_index("start")["duration_hours"].resample("W").sum()
    weekly_counts = df.set_index("start").resample("W").size()
    full_range = pd.date_range(weekly_hours.index.min(), weekly_hours.index.max(), freq="W")
    return pd.DataFrame({
        "total_hours": weekly_hours.reindex(full_range, fill_value=0),
        "event_count": weekly_counts.reindex(full_range, fill_value=0),
    })


def _z_scores(hours: pd.Series) -> pd.Series | None:
    """Z-scores computed on log1p(hours) rather than raw hours. Weekly
    hours are bounded at 0 but not above, so a handful of unusually packed
    weeks (a work crunch, a trip) inflate the raw standard deviation
    enough that `mean - z_threshold*std` goes negative - at which point no
    week, however empty, can ever be `z_threshold` deviations *below* the
    mean, and "empty" detection silently stops firing while "packed" keeps
    working. log1p compresses that right skew so both directions stay
    meaningful. Returns None when there's no variation to score against."""
    log_hours = np.log1p(hours)
    mean, std = log_hours.mean(), log_hours.std()
    if not std:
        return None
    return (log_hours - mean) / std


def anomalous_weeks(df: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    """Weeks whose total scheduled hours are `z_threshold` standard
    deviations away from your own mean week (log-scaled - see
    `_z_scores`), flagged as packed/empty."""
    load = weekly_load(df)
    z_scores = _z_scores(load["total_hours"])
    if z_scores is None:
        return load.assign(z_score=0.0, label="typical").iloc[0:0]

    load["z_score"] = z_scores
    load["label"] = load["z_score"].apply(
        lambda z: "packed" if z >= z_threshold else ("empty" if z <= -z_threshold else "typical")
    )
    return load[load["label"] != "typical"].sort_values("z_score", ascending=False)


def weekly_load_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Total scheduled hours per ISO week, broken out by category (index =
    week, columns = category), gaps filled with 0."""
    df = timed_events(df)
    pivot = (
        df.set_index("start").groupby("category")["duration_hours"]
        .resample("W").sum().unstack(level=0).fillna(0)
    )
    full_range = pd.date_range(pivot.index.min(), pivot.index.max(), freq="W")
    return pivot.reindex(full_range, fill_value=0)


def category_anomalies(df: pd.DataFrame, z_threshold: float = 2.0, min_active_weeks: int = 4) -> pd.DataFrame:
    """Per-category weekly anomalies: each category is compared against its
    own mean/std week (so a naturally-quiet category like Dating isn't
    judged against Gym's baseline), flagged packed/empty the same way as
    `anomalous_weeks`, sorted by how extreme the anomaly is either way.
    Categories active in fewer than `min_active_weeks` weeks are skipped -
    with only one or two data points, a single ordinary event trivially
    reads as an extreme z-score."""
    pivot = weekly_load_by_category(df)
    rows = []
    for category in pivot.columns:
        series = pivot[category]
        if (series > 0).sum() < min_active_weeks:
            continue
        z = _z_scores(series)
        if z is None:
            continue
        for week, z_score in z.items():
            if z_score >= z_threshold:
                label = "packed"
            elif z_score <= -z_threshold:
                label = "empty"
            else:
                continue
            rows.append({
                "week": week, "category": category, "hours": series[week],
                "z_score": z_score, "label": label,
            })
    if not rows:
        return pd.DataFrame(columns=["week", "category", "hours", "z_score", "label"])
    result = pd.DataFrame(rows)
    return result.reindex(result["z_score"].abs().sort_values(ascending=False).index).reset_index(drop=True)
