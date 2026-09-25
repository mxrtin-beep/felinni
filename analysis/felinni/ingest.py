"""Load an events.json export (from CalendarExporter) into a tidy DataFrame.

The exporter's schema (see CalendarExporter/Sources/CalendarExporter/ExportedEvent.swift):
    id, title, notes, location, startDate, endDate, isAllDay, calendarTitle,
    calendarColorHex, attendees, isRecurring, url, noteTags

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
# isn't fully tagged with attendees in Calendar, e.g. "Dinner with John Doe,
# Jane Doe, and McLovin". Names are kept individually if they look like a
# proper name (capitalized) - candidates that don't ("lunch with the whole
# team") are simply dropped rather than voiding the whole list, since one
# odd token (trailing emoji, "and the twins", ...) shouldn't cost you every
# real name in a group hangout. Includes the curly apostrophe (U+2019) iOS
# autocorrect substitutes for a straight one in names like "O'Brien".
_WITH_PATTERN = re.compile(r"\bwith\s+(.+)$", re.IGNORECASE)
_NAME_LIKE = re.compile(r"^[A-Z][\w.'’-]*(\s+[A-Z][\w.'’-]*)*$")
_TRAILING_DECORATION = re.compile(r"[\s!?.…\U0001F300-\U0001FAFF☀-➿]+$")


def _people_from_title(title: str | None) -> list[str]:
    if not title:
        return []
    match = _WITH_PATTERN.search(title)
    if not match:
        return []
    names_str = _TRAILING_DECORATION.sub("", match.group(1))
    names_str = re.sub(r"\s*,?\s+and\s+", ", ", names_str, flags=re.IGNORECASE)
    candidates = [c.strip().rstrip(".,") for c in names_str.split(",")]
    return [c for c in candidates if c and _NAME_LIKE.match(c)]


def strip_with_suffix(title: str | None) -> str | None:
    """Drop a trailing "with A, B" people list from a title, e.g. "Drinks
    and Dinner with Leo, Valerie" -> "Drinks and Dinner". Used by
    felinni.recurring so the same recurring hangout with a varying guest
    list (or one occurrence tagged via attendees vs. another spelled out
    in the title) is recognized as one series rather than fragmented into
    one-off titles that never individually clear the occurrence threshold.
    Only strips when the trailing clause actually looks like a name list
    (reuses _people_from_title's own check) - "Meeting with the board"
    keeps its "with" clause since "the board" isn't a name.
    """
    if not title:
        return title
    match = _WITH_PATTERN.search(title)
    if not match or not _people_from_title(title):
        return title
    return title[:match.start()].rstrip()


def _people_for(row_attendees: list[str], note_tags: dict[str, list[str]], title: str | None) -> list[str]:
    people = set(row_attendees or [])
    for key in ("people", "with"):
        people.update(note_tags.get(key, []))
    # Always merged in, not just as a fallback when `people` is empty:
    # a group event can have only some attendees formally invited in
    # Calendar, with the rest named just in the title.
    people.update(_people_from_title(title))
    return sorted(people)


def _most_common_last_names(all_people_lists: list[list[str]]) -> dict[str, str]:
    """First name -> its most common "First Last" form across every event,
    used to guess a last name for a bare first-name tag. Names with no
    space (nothing to learn a last name from) never contribute."""
    from collections import Counter

    counters: dict[str, Counter] = {}
    for people in all_people_lists:
        for name in people:
            first, _, rest = name.partition(" ")
            if rest:
                counters.setdefault(first, Counter())[name] += 1
    return {first: counter.most_common(1)[0][0] for first, counter in counters.items()}


def _resolve_first_name_aliases(all_people_lists: list[list[str]]) -> list[list[str]]:
    """You often refer to the same person as both "Alice" and "Alice
    Smith" across different events, which otherwise splits one person into
    two in every people-based analysis. When a bare first name shows up in
    a *group* event (someone else is also on it), it's rewritten to that
    first name's most common full form elsewhere in the calendar - group
    events are usually tagged more casually (first names only), while a
    1:1 event's title/attendee is more reliably the exact name you use for
    that person, so a first-name-only entry there is left alone rather
    than risk conflating two different people who share a first name."""
    last_name_for = _most_common_last_names(all_people_lists)
    resolved = []
    for people in all_people_lists:
        is_group = len(people) > 1
        rewritten = [
            last_name_for[name] if is_group and " " not in name and name in last_name_for else name
            for name in people
        ]
        # dedupe in case resolving now matches a full name already in the
        # same list (e.g. tagged once as "Alice" via the title and once as
        # "Alice Smith" via attendees).
        resolved.append(sorted(dict.fromkeys(rewritten)))
    return resolved


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
    return load_events_from_records(raw)


def load_events_from_records(raw: list[dict]) -> pd.DataFrame:
    """Same normalization as `load_events`, but starting from an
    already-parsed list of event dicts in the exporter's schema rather than
    a file path - used to merge events.json with imported calendar sources
    (felinni.calendar_sources) into one dataset."""
    if not raw:
        return pd.DataFrame(columns=[
            "id", "title", "notes", "location", "start", "end", "duration_hours",
            "is_all_day", "calendar", "category", "calendar_color", "people", "n_people",
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
    df["people"] = _resolve_first_name_aliases(df["people"].tolist())
    df["n_people"] = df["people"].apply(len)

    # Older exports (before CalendarExporter captured calendar colors) won't
    # have this column at all.
    if "calendarColorHex" not in df.columns:
        df["calendarColorHex"] = None

    df = df.rename(columns={
        "calendarTitle": "calendar", "isAllDay": "is_all_day", "isRecurring": "is_recurring",
        "calendarColorHex": "calendar_color",
    })
    df["year"] = df["start"].dt.year
    df["month"] = df["start"].dt.month
    df["week"] = df["start"].dt.isocalendar().week.astype(int)
    df["weekday"] = df["start"].dt.day_name()
    df["season"] = df["month"].map(SEASON_BY_MONTH)

    return df[[
        "id", "title", "notes", "location", "start", "end", "duration_hours",
        "is_all_day", "calendar", "category", "calendar_color", "people", "n_people",
        "is_recurring", "url", "year", "month", "week", "weekday", "season",
    ]].sort_values("start").reset_index(drop=True)


def category_color_map(df: pd.DataFrame) -> dict[str, str]:
    """The most common calendar color per category, so the dashboard can
    reuse the same colors you already picked in Calendar.app instead of an
    arbitrary fixed palette. Categories with no captured color (older
    exports, or a category set purely via a `Category:` note tag rather
    than a dedicated calendar) are simply absent from the result."""
    colored = df.dropna(subset=["calendar_color"])
    if colored.empty:
        return {}
    mode_per_category = colored.groupby("category")["calendar_color"].agg(lambda s: s.mode().iat[0])
    return mode_per_category.to_dict()
