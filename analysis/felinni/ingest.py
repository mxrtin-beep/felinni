"""Load an events.json export (from CalendarExporter) into a tidy DataFrame.

The exporter's schema (see CalendarExporter/Sources/CalendarExporter/ExportedEvent.swift):
    id, title, notes, location, startDate, endDate, isAllDay, calendarTitle,
    attendees, isRecurring, url, noteTags

Events since ~2022 are assumed to reliably carry startDate/endDate, location,
and a calendar (category). Older/messier events may be missing location or
people; every downstream analysis is written to tolerate NaNs there rather
than dropping rows, since a sparse older history is still useful for trends.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SEASON_BY_MONTH = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}


def _people_for(row_attendees: list[str], note_tags: dict[str, list[str]]) -> list[str]:
    people = set(row_attendees or [])
    for key in ("people", "with"):
        people.update(note_tags.get(key, []))
    return sorted(people)


def _category_for(calendar_title: str, note_tags: dict[str, list[str]]) -> str:
    tagged = note_tags.get("category")
    if tagged:
        return tagged[0]
    return calendar_title


def _location_for(location: str | None, note_tags: dict[str, list[str]]) -> str | None:
    tagged = note_tags.get("location")
    if tagged:
        return tagged[0]
    return location


def load_events(path: str | Path) -> pd.DataFrame:
    """Load and normalize an events.json export into a DataFrame, one row per event."""
    raw = json.loads(Path(path).read_text())
    if not raw:
        return pd.DataFrame(columns=[
            "id", "title", "notes", "location", "start", "end", "duration_hours",
            "is_all_day", "calendar", "category", "people", "n_people",
            "is_recurring", "url", "year", "month", "week", "weekday", "season",
        ])

    # Plain DataFrame(raw), not json_normalize: noteTags is a nested dict we
    # want to keep as-is per row, not flattened into noteTags.category columns.
    df = pd.DataFrame(raw)
    df["start"] = pd.to_datetime(df["startDate"], utc=True).dt.tz_convert(None)
    df["end"] = pd.to_datetime(df["endDate"], utc=True).dt.tz_convert(None)
    df["duration_hours"] = (df["end"] - df["start"]).dt.total_seconds() / 3600.0

    note_tags = df["noteTags"].apply(lambda d: d or {})
    df["category"] = [
        _category_for(cal, tags) for cal, tags in zip(df["calendarTitle"], note_tags)
    ]
    df["location"] = [
        _location_for(loc, tags) for loc, tags in zip(df["location"], note_tags)
    ]
    df["people"] = [
        _people_for(att, tags) for att, tags in zip(df["attendees"], note_tags)
    ]
    df["n_people"] = df["people"].apply(len)

    df = df.rename(columns={"calendarTitle": "calendar", "isAllDay": "is_all_day", "isRecurring": "is_recurring"})
    df["year"] = df["start"].dt.year
    df["month"] = df["start"].dt.month
    df["week"] = df["start"].dt.isocalendar().week.astype(int)
    df["weekday"] = df["start"].dt.day_name()
    df["season"] = df["month"].map(SEASON_BY_MONTH)

    return df[[
        "id", "title", "notes", "location", "start", "end", "duration_hours",
        "is_all_day", "calendar", "category", "people", "n_people",
        "is_recurring", "url", "year", "month", "week", "weekday", "season",
    ]].sort_values("start").reset_index(drop=True)
