"""Cross-reference calendar events tagged as dates (category "Date"/"First
Date", or a `Category: Date` / `People:` note tag) with an external table
of conversation timestamps (e.g. exported from a Hinge/iMessage analysis
project), so "date happened" events link to the matching conversation
without hand-built lookup tables.

The external table just needs columns: `person` and `timestamp` (anything
parseable by pandas.to_datetime) — e.g. the last message before a date, or
a match-created timestamp. Everything else in that table is passed
through untouched.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_DATE_CATEGORIES = {"date", "first date"}


def date_events(df: pd.DataFrame, categories: set[str] = DEFAULT_DATE_CATEGORIES) -> pd.DataFrame:
    matches = df[df["category"].str.casefold().isin({c.casefold() for c in categories})].copy()
    matches["date_partner"] = matches["people"].apply(lambda p: p[0] if p else None)
    return matches


def link_to_external_table(
    df: pd.DataFrame,
    external: pd.DataFrame,
    categories: set[str] = DEFAULT_DATE_CATEGORIES,
    max_gap_days: float = 3.0,
) -> pd.DataFrame:
    """For each calendar date-event, find the closest external-table row for
    the same person within `max_gap_days`, and attach it as extra columns.

    Requires a `person`-tagged date event (via attendees or a `People:`
    note tag) to know who to match against in the external table.
    """
    events = date_events(df, categories)
    external = external.copy()
    external["timestamp"] = pd.to_datetime(external["timestamp"])

    linked_rows = []
    for _, event in events.iterrows():
        if not event["date_partner"]:
            continue
        candidates = external[external["person"].str.casefold() == event["date_partner"].casefold()]
        if candidates.empty:
            continue

        gaps = (candidates["timestamp"] - event["start"]).abs()
        best_idx = gaps.idxmin()
        if gaps.loc[best_idx] > pd.Timedelta(days=max_gap_days):
            continue

        linked = event.to_dict()
        linked.update({f"external_{k}": v for k, v in candidates.loc[best_idx].to_dict().items()})
        linked["gap_days"] = gaps.loc[best_idx].total_seconds() / 86400.0
        linked_rows.append(linked)

    return pd.DataFrame(linked_rows)
