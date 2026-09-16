"""Travel history: build a "places visited" timeline from events tagged
as travel (category "Travel"/"Flight"/"Trip" by default, or matched by
`Category: Travel` / `Trip: <place>` note tags)."""
from __future__ import annotations

import pandas as pd

DEFAULT_TRAVEL_CATEGORIES = {"travel", "flight", "trip"}


def travel_events(df: pd.DataFrame, categories: set[str] = DEFAULT_TRAVEL_CATEGORIES) -> pd.DataFrame:
    return df[df["category"].str.casefold().isin({c.casefold() for c in categories})]


def _trip_destination(row: pd.Series) -> str:
    tags = row.get("notes") or ""
    # Falls back to the location/title fields; a `Trip: <place>` note tag
    # is parsed upstream into `location` via felinni.ingest's noteTags handling
    # when present, so this is just the last-resort fallback.
    return row["location"] or row["title"]


def trip_timeline(df: pd.DataFrame, categories: set[str] = DEFAULT_TRAVEL_CATEGORIES, gap_days: int = 3) -> pd.DataFrame:
    """Collapse travel events into distinct trips: consecutive travel events
    to the same destination within `gap_days` of each other are merged into
    one trip with a start/end date."""
    events = travel_events(df, categories).copy()
    if events.empty:
        return pd.DataFrame(columns=["destination", "start", "end", "n_events", "duration_days"])

    events["destination"] = events.apply(_trip_destination, axis=1)
    events = events.sort_values("start")

    trips = []
    current = None
    for _, row in events.iterrows():
        if current and row["destination"] == current["destination"] and (
            row["start"] - current["end"]
        ) <= pd.Timedelta(days=gap_days):
            current["end"] = max(current["end"], row["end"])
            current["n_events"] += 1
        else:
            if current:
                trips.append(current)
            current = {"destination": row["destination"], "start": row["start"], "end": row["end"], "n_events": 1}
    if current:
        trips.append(current)

    result = pd.DataFrame(trips)
    result["duration_days"] = (result["end"] - result["start"]).dt.total_seconds() / 86400.0
    return result.reset_index(drop=True)


def places_visited(df: pd.DataFrame, categories: set[str] = DEFAULT_TRAVEL_CATEGORIES) -> pd.DataFrame:
    """Unique destinations with first/last visit and trip count."""
    timeline = trip_timeline(df, categories)
    if timeline.empty:
        return timeline
    return timeline.groupby("destination").agg(
        trips=("start", "count"),
        first_visit=("start", "min"),
        last_visit=("end", "max"),
        total_days=("duration_days", "sum"),
    ).sort_values("first_visit")
