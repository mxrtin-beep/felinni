"""Tests for felinni.reconnect: reconnect suggestions built purely from
calendar history, narrowed to people you genuinely haven't seen anywhere
in at least two years - and, for upcoming events, to people you've also
hung out with before in that same category, and whose usual hangout
region (where geocoded) matches where the event is."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, reconnect


def _event(idx, title, start, end, people=None, is_all_day=False, category="Social", location=None):
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": None,
        "location": location,
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


AS_OF = pd.Timestamp("2024-08-01")
TWO_YEARS_AGO = AS_OF - pd.Timedelta(days=800)  # comfortably over the 2-year threshold
ONE_YEAR_AGO = AS_OF - pd.Timedelta(days=300)  # comfortably under it


@pytest.fixture
def revive_df():
    events = [
        # Book Club: a recurring series with Alice and Bob attending, that
        # stopped happening a long time ago. Alice hasn't been seen since
        # (over 2 years); Bob has been seen recently elsewhere.
        _event(1, "Book Club", "2022-01-01T19:00:00Z", "2022-01-01T20:00:00Z", people=["Alice", "Bob"]),
        _event(2, "Book Club", "2022-01-08T19:00:00Z", "2022-01-08T20:00:00Z", people=["Alice", "Bob"]),
        _event(3, "Book Club", "2022-01-15T19:00:00Z", "2022-01-15T20:00:00Z", people=["Alice", "Bob"]),
        _event(4, "Book Club", "2022-01-22T19:00:00Z", "2022-01-22T20:00:00Z", people=["Alice", "Bob"]),
        # Bob, seen recently at something unrelated.
        _event(5, "Lunch", ONE_YEAR_AGO.isoformat() + "Z", ONE_YEAR_AGO.isoformat() + "Z", people=["Bob"]),
    ]
    return ingest.load_events_from_records(events)


def test_suggested_invites_names_book_clubs_regulars(revive_df):
    result = reconnect.suggested_invites(revive_df, as_of=AS_OF)
    assert "Book Club" in result["series_title"].values
    book_club = result[result["series_title"] == "Book Club"]
    assert set(book_club["person"]) == {"Alice"}  # not Bob - seen too recently elsewhere


def test_suggested_invites_empty_when_nothing_has_faded(revive_df):
    result = reconnect.suggested_invites(revive_df, as_of=pd.Timestamp("2022-01-25"))
    assert result.empty


def test_suggested_invites_drops_people_seen_within_two_years():
    events = [
        _event(i, "Poker Night", (pd.Timestamp("2022-01-01") + pd.Timedelta(weeks=i)).isoformat() + "Z",
               (pd.Timestamp("2022-01-01") + pd.Timedelta(weeks=i)).isoformat() + "Z", people=["Carol"])
        for i in range(5)
    ] + [_event(99, "Coffee", ONE_YEAR_AGO.isoformat() + "Z", ONE_YEAR_AGO.isoformat() + "Z", people=["Carol"])]
    df = ingest.load_events_from_records(events)
    result = reconnect.suggested_invites(df, as_of=AS_OF)
    assert result.empty  # Carol was seen a year ago, well within the 2-year window


# --- upcoming_invite_suggestions ---

LA_CACHE_ENTRY = {"lat": 34.05, "lon": -118.24, "city": "Los Angeles", "country": "United States"}
BAY_CACHE_ENTRY = {"lat": 37.77, "lon": -122.42, "city": "San Francisco", "country": "United States"}
GEO_CACHE = {"Cafe A": LA_CACHE_ENTRY, "Cafe B": BAY_CACHE_ENTRY}


def _iso(ts):
    return ts.isoformat() + "Z"


def test_upcoming_invite_suggestions_requires_shared_category():
    events = [
        # Alice: a Social history with you, not seen in over 2 years.
        _event(1, "Coffee", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Alice"], category="Social"),
        # An upcoming Work event - Alice has never been in a Work event with you.
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty  # never hung out with Alice at a Work-category event


def test_upcoming_invite_suggestions_suggests_someone_from_the_same_category():
    events = [
        _event(1, "Standup", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert "Priya" in result["person"].values


def test_upcoming_invite_suggestions_drops_people_seen_within_two_years():
    events = [
        _event(1, "Standup", _iso(ONE_YEAR_AGO), _iso(ONE_YEAR_AGO), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty  # seen only a year ago


def test_upcoming_invite_suggestions_excludes_already_invited():
    events = [
        _event(1, "Standup", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               people=["Priya"], category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty


def test_upcoming_invite_suggestions_works_without_a_geocode_cache():
    # No location on either event, and no geocode cache - category + last
    # seen should still be enough to produce a suggestion.
    events = [
        _event(1, "Standup", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache={}, as_of=AS_OF)
    assert "Priya" in result["person"].values


def test_upcoming_invite_suggestions_matches_region_when_geocoded():
    events = [
        # Alice: always hangs out at Cafe A, in LA.
        _event(1, "Coffee", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Alice"], location="Cafe A"),
        # Erin: always hangs out at Cafe B, in the Bay Area - just as overdue
        # as Alice, but in the wrong place for an LA dinner.
        _event(2, "Coffee", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Erin"], location="Cafe B"),
        # An upcoming dinner at Cafe A (LA).
        _event(3, "Dinner", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), location="Cafe A"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF)
    assert "Alice" in result["person"].values
    assert "Erin" not in result["person"].values


def test_upcoming_invite_suggestions_ignores_events_outside_the_window():
    events = [
        _event(1, "Standup", _iso(TWO_YEARS_AGO), _iso(TWO_YEARS_AGO), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=30)), _iso(AS_OF + pd.Timedelta(days=30)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, days_ahead=5)
    assert result.empty  # the standup is ~30 days out, past a 5-day window
