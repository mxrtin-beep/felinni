"""Tests for felinni.recurring: cadence inference and falling-off status
for named recurring event series, detected from a repeated (and
participant-list-normalized) title rather than EventKit's own repeat-rule
flag."""
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


def test_default_as_of_uses_real_now_not_an_unrelated_future_event(tmp_path):
    # A real bug: defaulting as_of to the dataset's latest event date meant
    # an unrelated future-dated event (a recurring series' own
    # pre-materialized future instances, or just a flight you already
    # booked) could make an actively-ongoing series look "stopped" purely
    # because something else on the calendar was dated even later.
    now = pd.Timestamp.now().replace(hour=9, minute=0, second=0, microsecond=0)
    events = [_recurring_event(i, "Standup", now - pd.Timedelta(weeks=9) + pd.Timedelta(weeks=i)) for i in range(9)]
    events.append(_recurring_event(9, "Standup", now - pd.Timedelta(days=1)))
    events.append({
        "id": "evt-future", "title": "Future Flight", "notes": None, "location": None,
        "startDate": (now + pd.Timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": (now + pd.Timedelta(days=400, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isAllDay": False, "calendarTitle": "Travel", "calendarColorHex": None,
        "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
    })
    df = _load(tmp_path, events)

    result = recurring.recurring_series(df)  # no as_of passed - uses the real-now default
    row = result[result["title"] == "Standup"].iloc[0]
    assert row["status"] == "active"


def test_series_with_too_few_occurrences_is_skipped(tmp_path):
    start = pd.Timestamp("2024-01-01")
    events = [_recurring_event(i, "New Thing", start + pd.Timedelta(weeks=i)) for i in range(2)]
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df, min_occurrences=4)
    assert "New Thing" not in result["title"].values


def test_repeated_title_is_detected_even_without_native_recurring_flag(tmp_path):
    # A real habit (a gym rotation typed in fresh each time, e.g. "Push
    # Day") is often never set up as a formal Calendar repeat rule at all -
    # relying on isRecurring alone would silently drop it.
    events = [_recurring_event(i, "Push Day", pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=i)) for i in range(5)]
    for e in events:
        e["isRecurring"] = False
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df)
    assert "Push Day" in result["title"].values
    assert result[result["title"] == "Push Day"].iloc[0]["occurrences"] == 5


def test_with_suffix_is_stripped_so_varying_guests_count_as_one_series(tmp_path):
    # "Dinner with Alice" and "Dinner with Bob" are the same recurring
    # hangout with a different guest attached each time, not two
    # unrelated one-off titles that individually fall short of
    # min_occurrences.
    start = pd.Timestamp("2024-01-01")
    events = [
        _recurring_event(0, "Dinner with Alice", start),
        _recurring_event(1, "Dinner with Bob", start + pd.Timedelta(weeks=1)),
        _recurring_event(2, "Dinner with Alice", start + pd.Timedelta(weeks=2)),
        _recurring_event(3, "Dinner with Bob", start + pd.Timedelta(weeks=3)),
    ]
    for e in events:
        e["isRecurring"] = False
    df = _load(tmp_path, events)
    result = recurring.recurring_series(df, min_occurrences=4)
    assert "Dinner" in result["title"].values
    assert "Dinner with Alice" not in result["title"].values
    assert result[result["title"] == "Dinner"].iloc[0]["occurrences"] == 4


@pytest.mark.parametrize("days,expected", [(1, "daily"), (7, "weekly"), (14, "biweekly"), (30, "monthly"), (90, "quarterly"), (365, "yearly")])
def test_cadence_label(days, expected):
    assert recurring.cadence_label(days) == expected
