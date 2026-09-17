"""Tests for felinni.social: all-day events (a full-day placeholder like a
birthday, or a multi-day trip logged as one block) shouldn't count toward
time-with-a-person or event frequency the way a timed event does."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, social


def _event(idx, title, start, end, people=None, is_all_day=False, category="Social"):
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": None,
        "location": None,
        "startDate": start,
        "endDate": end,
        "isAllDay": is_all_day,
        "calendarTitle": category,
        "calendarColorHex": None,
        "attendees": people or [],
        "isRecurring": False,
        "url": None,
        "noteTags": {},
    }


@pytest.fixture
def df():
    events = [
        _event(1, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", people=["Alice"]),
        _event(2, "Coffee", "2024-01-08T09:00:00Z", "2024-01-08T10:00:00Z", people=["Alice"]),
        # An all-day trip with Alice tagged - nominally a ~24h block, which
        # would otherwise dwarf the two real 1-hour coffee dates above.
        _event(3, "Trip with Alice", "2024-02-01T00:00:00Z", "2024-02-03T00:00:00Z",
               people=["Alice"], is_all_day=True, category="Travel"),
        _event(4, "Birthday", "2024-03-01T00:00:00Z", "2024-03-02T00:00:00Z",
               people=["Bob"], is_all_day=True, category="Personal"),
    ]
    return ingest.load_events_from_records(events)


def test_person_frequency_excludes_all_day_events(df):
    freq = social.person_frequency(df)
    assert freq.loc["Alice", "events"] == 2  # not 3 - the all-day trip is excluded
    assert freq.loc["Alice", "total_hours"] == 2.0  # two 1-hour coffees, not the ~48h trip
    assert "Bob" not in freq.index  # Bob only appears on an all-day event


def test_social_time_share_ignores_all_day_hours(df):
    share = social.social_time_share(df)
    assert share.loc["Alice", "total_hours"] == 2.0


def test_person_trend_by_period_excludes_all_day_events(df):
    trend = social.person_trend_by_period(df, granularity="year")
    assert trend.loc[trend.index[0], "Alice"] == 2
    assert "Bob" not in trend.columns
