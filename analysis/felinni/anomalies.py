"""Anomaly spotting: weeks that were unusually packed or unusually empty,
as a rough proxy for stress or low periods."""
from __future__ import annotations

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


def anomalous_weeks(df: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    """Weeks whose total scheduled hours are `z_threshold` standard
    deviations away from your own mean week, flagged as packed/empty."""
    load = weekly_load(df)
    mean, std = load["total_hours"].mean(), load["total_hours"].std()
    if not std:
        return load.assign(z_score=0.0, label="typical").iloc[0:0]

    load["z_score"] = (load["total_hours"] - mean) / std
    load["label"] = load["z_score"].apply(
        lambda z: "packed" if z >= z_threshold else ("empty" if z <= -z_threshold else "typical")
    )
    return load[load["label"] != "typical"].sort_values("z_score", ascending=False)
