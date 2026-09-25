"""Tests for felinni.reconnect: who to invite back to your own fading
recurring events, and - for events already on your calendar in the next
two weeks - who to invite. Both are capped at people last seen within two
years (MAX_DAYS_SINCE_SEEN): a ceiling, not a floor, since someone you
haven't shared an event with in years longer than that has likely drifted
out of your life for a reason and isn't a useful "reconnect" suggestion.
Upcoming events are further narrowed to people you've also hung out with
before in that same category, and whose usual hangout region (where
geocoded) matches where the event is."""
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


def _iso(ts):
    return ts.isoformat() + "Z"


AS_OF = pd.Timestamp("2024-08-01")
WITHIN_CAP = AS_OF - pd.Timedelta(days=400)  # comfortably under the 2-year (730-day) cap
OVER_CAP = AS_OF - pd.Timedelta(days=1000)  # comfortably over it - a "drifted out of your life" contact


@pytest.fixture
def revive_df():
    events = [
        # Book Club: a recurring series with Alice and Bob attending, that
        # stopped happening long ago. Alice hasn't been seen anywhere
        # since (over the 2-year cap) - not a useful suggestion. Bob was
        # also in Book Club, but has been seen more recently (within the
        # cap) at something else, and should still show up.
        _event(1, "Book Club", _iso(OVER_CAP - pd.Timedelta(weeks=3)), _iso(OVER_CAP - pd.Timedelta(weeks=3)), people=["Alice", "Bob"]),
        _event(2, "Book Club", _iso(OVER_CAP - pd.Timedelta(weeks=2)), _iso(OVER_CAP - pd.Timedelta(weeks=2)), people=["Alice", "Bob"]),
        _event(3, "Book Club", _iso(OVER_CAP - pd.Timedelta(weeks=1)), _iso(OVER_CAP - pd.Timedelta(weeks=1)), people=["Alice", "Bob"]),
        _event(4, "Book Club", _iso(OVER_CAP), _iso(OVER_CAP), people=["Alice", "Bob"]),
        _event(5, "Lunch", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Bob"]),
    ]
    return ingest.load_events_from_records(events)


def test_suggested_invites_excludes_regulars_not_seen_within_the_cap(revive_df):
    result = reconnect.suggested_invites(revive_df, as_of=AS_OF)
    assert "Book Club" in result["series_title"].values
    book_club = result[result["series_title"] == "Book Club"]
    assert set(book_club["person"]) == {"Bob"}  # not Alice - over the 2-year cap


def test_suggested_invites_ranks_more_overdue_regular_first():
    events = [
        _event(i, "Poker Night", _iso(AS_OF - pd.Timedelta(days=800) + pd.Timedelta(weeks=i)),
               _iso(AS_OF - pd.Timedelta(days=800) + pd.Timedelta(weeks=i)), people=["Dave", "Erin"])
        for i in range(4)
    ] + [
        # Dave: not seen since (further out, but still within the cap).
        # Erin: seen more recently, also within the cap.
        _event(10, "Coffee", _iso(AS_OF - pd.Timedelta(days=700)), _iso(AS_OF - pd.Timedelta(days=700)), people=["Dave"]),
        _event(11, "Coffee", _iso(AS_OF - pd.Timedelta(days=100)), _iso(AS_OF - pd.Timedelta(days=100)), people=["Erin"]),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.suggested_invites(df, as_of=AS_OF)
    poker = result[result["series_title"] == "Poker Night"]
    assert list(poker["person"]) == ["Dave", "Erin"]


def test_suggested_invites_empty_when_nothing_has_faded(revive_df):
    result = reconnect.suggested_invites(revive_df, as_of=OVER_CAP + pd.Timedelta(days=4))
    assert result.empty


# --- upcoming_invite_suggestions ---

LA_CACHE_ENTRY = {"lat": 34.05, "lon": -118.24, "city": "Los Angeles", "country": "United States"}
BAY_CACHE_ENTRY = {"lat": 37.77, "lon": -122.42, "city": "San Francisco", "country": "United States"}
GEO_CACHE = {"Cafe A": LA_CACHE_ENTRY, "Cafe B": BAY_CACHE_ENTRY}


def test_upcoming_invite_suggestions_requires_shared_category():
    events = [
        # Alice: a Social history with you, within the 2-year cap.
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Alice"], category="Social"),
        # An upcoming Work event - Alice has never been in a Work event with you.
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty  # never hung out with Alice at a Work-category event


def test_upcoming_invite_suggestions_suggests_someone_from_the_same_category():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert "Priya" in result["person"].values
    reason = result[result["person"] == "Priya"].iloc[0]["reason"]
    assert "Work" in reason  # explains why Priya, not just that she's overdue


def test_upcoming_invite_suggestions_excludes_people_not_seen_within_the_cap():
    events = [
        _event(1, "Standup", _iso(OVER_CAP), _iso(OVER_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty  # hasn't been seen in over two years - drifted, not a useful suggestion


def test_upcoming_invite_suggestions_excludes_already_invited():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
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
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache={}, as_of=AS_OF)
    assert "Priya" in result["person"].values


def test_upcoming_invite_suggestions_matches_region_when_geocoded():
    events = [
        # Alice: always hangs out at Cafe A, in LA.
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Alice"], location="Cafe A"),
        # Erin: always hangs out at Cafe B, in the Bay Area - just as overdue
        # as Alice, but in the wrong place for an LA dinner.
        _event(2, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Erin"], location="Cafe B"),
        # An upcoming dinner at Cafe A (LA).
        _event(3, "Dinner", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), location="Cafe A"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF)
    assert "Alice" in result["person"].values
    assert "Erin" not in result["person"].values
    row = result[result["person"] == "Alice"].iloc[0]
    assert row["event_location"] == "Cafe A"  # the raw calendar location, not just the derived region


def test_upcoming_invite_suggestions_excludes_unknown_region_when_event_region_is_known():
    # Frank shares a category with the upcoming event and is otherwise
    # overdue, but every event you've shared with him is at an
    # ungeocoded location - his usual region is simply unknown. He should
    # be excluded, not let through by default just because there's no
    # region on file to conflict with.
    events = [
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Frank"], location="Some Cafe"),
        # A past, solo event at Cafe A so the location->metro map actually
        # knows about it (only locations that appear in past history are
        # clustered) - without this, the upcoming event's own region would
        # come back unknown too, and never exercise the filter at all.
        _event(2, "Solo errand", _iso(WITHIN_CAP), _iso(WITHIN_CAP), location="Cafe A"),
        _event(3, "Dinner", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), location="Cafe A"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF)
    assert "Frank" not in result["person"].values


def test_upcoming_invite_suggestions_ignores_events_outside_the_window():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=30)), _iso(AS_OF + pd.Timedelta(days=30)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, days_ahead=5)
    assert result.empty  # the standup is ~30 days out, past a 5-day window


def test_upcoming_invite_suggestions_default_window_is_two_weeks():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=20)), _iso(AS_OF + pd.Timedelta(days=20)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF)
    assert result.empty  # 20 days out, past the default 14-day window


def test_upcoming_invite_suggestions_notes_when_the_events_own_location_isnt_geocoded():
    events = [
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location="Some Untracked Office"),
    ]
    df = ingest.load_events_from_records(events)
    # A non-empty cache that simply doesn't cover this event's own location.
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF)
    row = result[result["person"] == "Priya"].iloc[0]
    assert row["region"] is None
    assert "isn't geocoded" in row["reason"]


def test_upcoming_invite_suggestions_spreads_suggestions_across_events():
    # Alice and Bob both share a Social history with you and are both
    # within the 2-year cap, with Alice more overdue than Bob. Two
    # upcoming Social events with no location (category is the only
    # filter) should each get a different person, not both defaulting to
    # Alice just because she's the single most-overdue candidate overall.
    events = [
        _event(1, "Coffee", _iso(AS_OF - pd.Timedelta(days=700)), _iso(AS_OF - pd.Timedelta(days=700)), people=["Alice"]),
        _event(2, "Coffee", _iso(AS_OF - pd.Timedelta(days=200)), _iso(AS_OF - pd.Timedelta(days=200)), people=["Bob"]),
        _event(3, "Dinner", _iso(AS_OF + pd.Timedelta(days=3)), _iso(AS_OF + pd.Timedelta(days=3))),
        _event(4, "Party", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5))),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, top_n=1)
    people_by_event = dict(zip(result["event_title"], result["person"]))
    assert people_by_event["Dinner"] == "Alice"  # the sooner event gets first pick of the most overdue
    assert people_by_event["Party"] == "Bob"  # Alice already used - Bob gets a turn instead of a repeat
