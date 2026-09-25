"""Tests for felinni.reconnect: reconnect suggestions built purely from
calendar history - who's overdue relative to their own usual cadence, which
recurring events have faded, and who to invite back to each."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, reconnect


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
        # Alice: seen every week for 4 weeks, then nothing for a long stretch -
        # should read as heavily overdue.
        _event(1, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", people=["Alice"]),
        _event(2, "Coffee", "2024-01-08T09:00:00Z", "2024-01-08T10:00:00Z", people=["Alice"]),
        _event(3, "Coffee", "2024-01-15T09:00:00Z", "2024-01-15T10:00:00Z", people=["Alice"]),
        _event(4, "Coffee", "2024-01-22T09:00:00Z", "2024-01-22T10:00:00Z", people=["Alice"]),
        # Carol: seen roughly once a year, and it's only been a few months since -
        # not overdue relative to her own (infrequent) usual cadence.
        _event(5, "Annual Catchup", "2022-06-01T09:00:00Z", "2022-06-01T10:00:00Z", people=["Carol"]),
        _event(6, "Annual Catchup", "2023-06-01T09:00:00Z", "2023-06-01T10:00:00Z", people=["Carol"]),
        # Dave: appears only once, so there's no interval to infer - dropped
        # by the default min_events=2.
        _event(7, "One-off Lunch", "2024-01-01T12:00:00Z", "2024-01-01T13:00:00Z", people=["Dave"]),
        # Book Club: a recurring series with Alice and Bob attending, that
        # stopped happening a long time ago (relative to its own weekly pace).
        _event(8, "Book Club", "2023-01-01T19:00:00Z", "2023-01-01T20:00:00Z", people=["Alice", "Bob"]),
        _event(9, "Book Club", "2023-01-08T19:00:00Z", "2023-01-08T20:00:00Z", people=["Alice", "Bob"]),
        _event(10, "Book Club", "2023-01-15T19:00:00Z", "2023-01-15T20:00:00Z", people=["Alice", "Bob"]),
        _event(11, "Book Club", "2023-01-22T19:00:00Z", "2023-01-22T20:00:00Z", people=["Alice", "Bob"]),
    ]
    return ingest.load_events_from_records(events)


AS_OF = pd.Timestamp("2024-08-01")


def test_people_to_reconnect_with_ranks_by_overdue_ratio_not_raw_days(df):
    result = reconnect.people_to_reconnect_with(df, as_of=AS_OF).set_index("person")
    # Alice's usual gap is ~7 days and it's been ~7 months - wildly overdue.
    # Carol's usual gap is ~1 year and it's been ~1 year 2 months - barely overdue.
    assert result.loc["Alice", "overdue_ratio"] > result.loc["Carol", "overdue_ratio"]
    assert "Dave" not in result.index  # only one event - no interval to compare against


def test_people_to_reconnect_with_includes_recent_event_titles(df):
    result = reconnect.people_to_reconnect_with(df, as_of=AS_OF).set_index("person")
    assert "Coffee" in result.loc["Alice", "recent_events"]


def test_events_to_revive_flags_stopped_series(df):
    result = reconnect.events_to_revive(df, as_of=AS_OF)
    assert "Book Club" in result["title"].values
    assert result[result["title"] == "Book Club"].iloc[0]["status"] == "stopped"


def test_events_to_revive_excludes_still_active_series(df):
    result = reconnect.events_to_revive(df, as_of=pd.Timestamp("2023-01-25"))
    assert "Book Club" not in result["title"].values  # still going, not faded


def test_suggested_invites_names_book_clubs_regulars(df):
    result = reconnect.suggested_invites(df, as_of=AS_OF)
    book_club = result[result["series_title"] == "Book Club"]
    assert set(book_club["person"]) == {"Alice", "Bob"}
    for _, row in book_club.iterrows():
        assert row["times_attended"] == 4


def test_suggested_invites_empty_when_nothing_has_faded(df):
    result = reconnect.suggested_invites(df, as_of=pd.Timestamp("2023-01-25"))
    assert result.empty


# --- upcoming_invite_suggestions: geography-aware invite suggestions for
# events already on the calendar ---

LA_CACHE_ENTRY = {"lat": 34.05, "lon": -118.24, "city": "Los Angeles", "country": "United States"}
BAY_CACHE_ENTRY = {"lat": 37.77, "lon": -122.42, "city": "San Francisco", "country": "United States"}
GEO_CACHE = {"Cafe A": LA_CACHE_ENTRY, "Cafe B": BAY_CACHE_ENTRY}


def _located_event(idx, title, start, end, location, people=None, is_all_day=False, category="Social"):
    event = _event(idx, title, start, end, people=people, is_all_day=is_all_day, category=category)
    event["location"] = location
    return event


@pytest.fixture
def geo_df():
    events = [
        # Alice: always hangs out at Cafe A, in LA.
        _located_event(1, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(2, "Coffee", "2024-01-08T09:00:00Z", "2024-01-08T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(3, "Coffee", "2024-01-15T09:00:00Z", "2024-01-15T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(4, "Coffee", "2024-01-22T09:00:00Z", "2024-01-22T10:00:00Z", "Cafe A", people=["Alice"]),
        # Erin: always hangs out at Cafe B, in the Bay Area - just as overdue
        # as Alice, but in the wrong place for an LA event.
        _located_event(5, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", "Cafe B", people=["Erin"]),
        _located_event(6, "Coffee", "2024-01-08T09:00:00Z", "2024-01-08T10:00:00Z", "Cafe B", people=["Erin"]),
        _located_event(7, "Coffee", "2024-01-15T09:00:00Z", "2024-01-15T10:00:00Z", "Cafe B", people=["Erin"]),
        _located_event(8, "Coffee", "2024-01-22T09:00:00Z", "2024-01-22T10:00:00Z", "Cafe B", people=["Erin"]),
        # An upcoming, already-scheduled dinner at Cafe A (LA).
        _located_event(9, "Dinner", "2024-08-15T19:00:00Z", "2024-08-15T20:30:00Z", "Cafe A"),
    ]
    return ingest.load_events_from_records(events)


def test_upcoming_invite_suggestions_matches_region_not_just_overdue(geo_df):
    result = reconnect.upcoming_invite_suggestions(geo_df, GEO_CACHE, as_of=AS_OF)
    dinner = result[result["event_title"] == "Dinner"]
    assert "Alice" in dinner["person"].values  # usually seen at Cafe A, in LA - same region as the event
    assert "Erin" not in dinner["person"].values  # usually seen at Cafe B, in the Bay Area - wrong region


def test_upcoming_invite_suggestions_empty_without_geocode_cache(geo_df):
    result = reconnect.upcoming_invite_suggestions(geo_df, {}, as_of=AS_OF)
    assert result.empty


def test_upcoming_invite_suggestions_excludes_already_invited(geo_df):
    events = ingest.load_events_from_records([
        _located_event(1, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(2, "Coffee", "2024-01-08T09:00:00Z", "2024-01-08T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(3, "Coffee", "2024-01-15T09:00:00Z", "2024-01-15T10:00:00Z", "Cafe A", people=["Alice"]),
        _located_event(4, "Coffee", "2024-01-22T09:00:00Z", "2024-01-22T10:00:00Z", "Cafe A", people=["Alice"]),
        # Alice is already on the upcoming event's own guest list.
        _located_event(5, "Dinner", "2024-08-15T19:00:00Z", "2024-08-15T20:30:00Z", "Cafe A", people=["Alice"]),
    ])
    result = reconnect.upcoming_invite_suggestions(events, GEO_CACHE, as_of=AS_OF)
    assert result.empty


def test_upcoming_invite_suggestions_ignores_events_outside_the_window(geo_df):
    result = reconnect.upcoming_invite_suggestions(geo_df, GEO_CACHE, as_of=AS_OF, days_ahead=5)
    assert result.empty  # the dinner is ~14 days out, past a 5-day window
