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
import re
from pathlib import Path

import pandas as pd

SEASON_BY_MONTH = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}

# Matches a location that's actually a meeting link or phone number rather
# than a place - these would otherwise pollute place-frequency/map results
# with one-off junk like "https://zoom.us/j/123" or "+1 415-555-0100".
_JUNK_LOCATION_PATTERNS = [
    re.compile(r"zoom\.us", re.IGNORECASE),
    re.compile(r"meet\.google\.com", re.IGNORECASE),
    re.compile(r"teams\.microsoft\.com", re.IGNORECASE),
    re.compile(r"teams\.live\.com", re.IGNORECASE),
    re.compile(r"webex\.com", re.IGNORECASE),
    re.compile(r"^https?://", re.IGNORECASE),
    re.compile(r"^[\d\s()+.\-]{7,}$"),  # phone numbers: mostly digits/punctuation
]


def _is_junk_location(location: str) -> bool:
    return any(pattern.search(location) for pattern in _JUNK_LOCATION_PATTERNS)


# Captures a trailing "with A, B, and C" name list on an event title that
# isn't tagged with attendees in Calendar, e.g. "Dinner with John Doe, Jane
# Doe, and McLovin". Only trusted as people if every candidate looks like a
# proper name (capitalized) - "lunch with the whole team" is left alone.
_WITH_PATTERN = re.compile(r"\bwith\s+(.+)$", re.IGNORECASE)
_NAME_LIKE = re.compile(r"^[A-Z][\w.'-]*(\s+[A-Z][\w.'-]*)*$")


def _people_from_title(title: str | None) -> list[str]:
    if not title:
        return []
    match = _WITH_PATTERN.search(title)
    if not match:
        return []
    names_str = match.group(1).strip().rstrip(".")
    names_str = re.sub(r"\s*,?\s+and\s+", ", ", names_str, flags=re.IGNORECASE)
    candidates = [c.strip() for c in names_str.split(",") if c.strip()]
    if not candidates or not all(_NAME_LIKE.match(c) for c in candidates):
        return []
    return candidates


def _people_for(row_attendees: list[str], note_tags: dict[str, list[str]], title: str | None) -> list[str]:
    people = set(row_attendees or [])
    for key in ("people", "with"):
        people.update(note_tags.get(key, []))
    if not people:
        people.update(_people_from_title(title))
    return sorted(people)


def _category_for(calendar_title: str, note_tags: dict[str, list[str]]) -> str:
    tagged = note_tags.get("category")
    if tagged:
        return tagged[0]
    return calendar_title


def _location_for(location: str | None, note_tags: dict[str, list[str]]) -> str | None:
    tagged = note_tags.get("location")
    resolved = tagged[0] if tagged else location
    if isinstance(resolved, str) and _is_junk_location(resolved):
        return None
    return resolved


def timed_events(df: pd.DataFrame) -> pd.DataFrame:
    """Events with a specific start/end time - excludes all-day entries
    (vacations, birthdays, holidays, ...) that don't carry a real duration
    and would otherwise skew anything counting hours or daily/weekly load."""
    return df[~df["is_all_day"]]


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
        _people_for(att, tags, title) for att, tags, title in zip(df["attendees"], note_tags, df["title"])
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
