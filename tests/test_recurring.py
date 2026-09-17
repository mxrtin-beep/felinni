"""Tests for felinni.recurring: cadence inference and falling-off status
for named recurring event series (EventKit's own repeat rule, not just a
shared category)."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, recurring


def _recurring_event(idx, title, start):
    end = start.replace(hour=start.hour + 1) if start.hour < 23 else start
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": None,
        "location": None,
        "startDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isAllDay": False,
        "calendarTitle": "Social",
        "calendarColorHex": None,
        "attendees": [],
        "isRecurring": True,
        "url": None,
        "noteTags": {},
    }


def _load(tmp_path, events):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(events))
    return ingest.load_events(path)


def test_active_weekly_series_is_flagged_active(tmp_path):
    start = pd.Timestamp("2024-01-01")
    events = [_recurring_event(i, "Book Club", start + pd.Timedelta(weeks=i)) for i in range(10)]
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df, as_of=start + pd.Timedelta(weeks=9, days=2))
    row = result[result["title"] == "Book Club"].iloc[0]
    assert row["status"] == "active"
    assert row["cadence"] == "weekly"


def test_stopped_series_is_flagged_stopped(tmp_path):
    start = pd.Timestamp("2021-01-01")
    events = [_recurring_event(i, "Poker Night", start + pd.Timedelta(weeks=i)) for i in range(10)]
    df = _load(tmp_path, events)
    # "as_of" is a year after the last occurrence - way past a weekly cadence
    result = recurring.recurring_series(df, as_of=start + pd.Timedelta(weeks=9) + pd.Timedelta(days=365))
    row = result[result["title"] == "Poker Night"].iloc[0]
    assert row["status"] == "stopped"


def test_slowing_down_series_is_flagged_between_active_and_stopped(tmp_path):
    start = pd.Timestamp("2023-01-01")
    events = [_recurring_event(i, "Standup", start + pd.Timedelta(weeks=i)) for i in range(10)]
    df = _load(tmp_path, events)
    # About 2.5x the weekly cadence since the last occurrence.
    result = recurring.recurring_series(df, as_of=start + pd.Timedelta(weeks=9, days=17))
    row = result[result["title"] == "Standup"].iloc[0]
    assert row["status"] == "slowing down"


def test_streak_days_spans_first_to_last_occurrence(tmp_path):
    start = pd.Timestamp("2024-01-01")
    events = [_recurring_event(i, "Book Club", start + pd.Timedelta(weeks=i)) for i in range(10)]
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df, as_of=start + pd.Timedelta(weeks=9, days=2))
    row = result[result["title"] == "Book Club"].iloc[0]
    assert row["streak_days"] == 9 * 7


def test_series_with_too_few_occurrences_is_skipped(tmp_path):
    start = pd.Timestamp("2024-01-01")
    events = [_recurring_event(i, "New Thing", start + pd.Timedelta(weeks=i)) for i in range(2)]
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df, min_occurrences=4)
    assert "New Thing" not in result["title"].values


def test_non_recurring_events_are_excluded(tmp_path):
    events = [_recurring_event(i, "Book Club", pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=i)) for i in range(5)]
    for e in events:
        e["isRecurring"] = False
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df)
    assert result.empty


@pytest.mark.parametrize("days,expected", [(1, "daily"), (7, "weekly"), (14, "biweekly"), (30, "monthly"), (90, "quarterly"), (365, "yearly")])
def test_cadence_label(days, expected):
    assert recurring.cadence_label(days) == expected
