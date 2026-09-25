"""Tests for felinni.reconnect: for located events already on your
calendar in the next two weeks, who to invite. Capped at people last seen
within two years (MAX_DAYS_SINCE_SEEN): a ceiling, not a floor, since
someone you haven't shared an event with in years longer than that has
likely drifted out of your life for a reason and isn't a useful
"reconnect" suggestion. Further narrowed to people you've also hung out
with before in that same category, and whose usual hangout region (where
geocoded) matches where the event is. Among whoever qualifies, the picks
are random (seeded here for reproducibility) rather than most-overdue-first."""
import sys
from pathlib import Path

import pandas as pd

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

# A generic, ungeocoded location for tests that don't care about region
# matching (just need *some* location, since events without one are now
# excluded entirely).
SOME_VENUE = "Some Venue"

LA_CACHE_ENTRY = {"lat": 34.05, "lon": -118.24, "city": "Los Angeles", "country": "United States"}
BAY_CACHE_ENTRY = {"lat": 37.77, "lon": -122.42, "city": "San Francisco", "country": "United States"}
GEO_CACHE = {"Cafe A": LA_CACHE_ENTRY, "Cafe B": BAY_CACHE_ENTRY}


def test_upcoming_events_excludes_events_without_a_location():
    events = [
        _event(1, "No Location Meetup", _iso(AS_OF + pd.Timedelta(days=2)), _iso(AS_OF + pd.Timedelta(days=2))),
        _event(2, "Dinner", _iso(AS_OF + pd.Timedelta(days=3)), _iso(AS_OF + pd.Timedelta(days=3)), location="Cafe A"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_events(df, as_of=AS_OF)
    assert list(result["title"]) == ["Dinner"]


def test_upcoming_invite_suggestions_excludes_events_without_a_location():
    events = [
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        # No location - nothing to invite anyone "to" geographically.
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert result.empty


def test_upcoming_invite_suggestions_requires_shared_category():
    events = [
        # Alice: a Social history with you, within the 2-year cap.
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Alice"], category="Social"),
        # An upcoming Work event - Alice has never been in a Work event with you.
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert result.empty  # never hung out with Alice at a Work-category event


def test_upcoming_invite_suggestions_suggests_someone_from_the_same_category():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"],
               category="Work", location="Office"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert "Priya" in result["person"].values
    row = result[result["person"] == "Priya"].iloc[0]
    # The last real event actually had with Priya, as concrete context.
    assert row["last_event_title"] == "Standup"
    assert row["last_event_location"] == "Office"
    assert pd.Timestamp(row["last_event_date"]) == WITHIN_CAP


def test_upcoming_invite_suggestions_last_event_location_is_none_when_unset():
    events = [
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),  # no location
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    row = result[result["person"] == "Priya"].iloc[0]
    assert row["last_event_title"] == "Coffee"
    assert row["last_event_location"] is None


def test_upcoming_invite_suggestions_max_days_since_seen_is_adjustable():
    events = [
        _event(1, "Coffee", _iso(AS_OF - pd.Timedelta(days=100)), _iso(AS_OF - pd.Timedelta(days=100)),
               people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    # Default 2-year cap: 100 days ago is well within it.
    default_result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert "Priya" in default_result["person"].values
    # A shorter, caller-supplied cap excludes her.
    short_cap_result = reconnect.upcoming_invite_suggestions(
        df, geocode_cache=None, as_of=AS_OF, seed=0, max_days_since_seen=30,
    )
    assert short_cap_result.empty


def test_upcoming_invite_suggestions_excludes_people_not_seen_within_the_cap():
    events = [
        _event(1, "Standup", _iso(OVER_CAP), _iso(OVER_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert result.empty  # hasn't been seen in over two years - drifted, not a useful suggestion


def test_upcoming_invite_suggestions_excludes_already_invited():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               people=["Priya"], category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert result.empty


def test_upcoming_invite_suggestions_works_without_a_geocode_cache():
    # No geocode cache at all - category + last seen should still be
    # enough to produce a suggestion (the event still needs a location to
    # be considered at all, just not a geocoded one).
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache={}, as_of=AS_OF, seed=0)
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
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF, seed=0)
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
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF, seed=0)
    assert "Frank" not in result["person"].values


def test_upcoming_invite_suggestions_ignores_events_outside_the_window():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=30)), _iso(AS_OF + pd.Timedelta(days=30)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, days_ahead=5, seed=0)
    assert result.empty  # the standup is ~30 days out, past a 5-day window


def test_upcoming_invite_suggestions_default_window_is_two_weeks():
    events = [
        _event(1, "Standup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=20)), _iso(AS_OF + pd.Timedelta(days=20)),
               category="Work", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, seed=0)
    assert result.empty  # 20 days out, past the default 14-day window


def test_upcoming_invite_suggestions_falls_back_to_category_when_not_geocoded():
    events = [
        _event(1, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Priya"], category="Work"),
        _event(2, "Standup", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Work", location="Some Untracked Office"),
    ]
    df = ingest.load_events_from_records(events)
    # A non-empty cache that simply doesn't cover this event's own location,
    # and whose text doesn't name any known city either - Priya still
    # qualifies on category alone.
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF, seed=0)
    row = result[result["person"] == "Priya"].iloc[0]
    assert row["region"] is None


def test_upcoming_invite_suggestions_spreads_suggestions_across_events():
    # Alice and Bob both share a Social history with you and are both
    # within the 2-year cap. Two upcoming Social events, each wanting only
    # one invite, should get a different person each rather than both
    # landing on the same one of the two.
    events = [
        _event(1, "Coffee", _iso(AS_OF - pd.Timedelta(days=700)), _iso(AS_OF - pd.Timedelta(days=700)), people=["Alice"]),
        _event(2, "Coffee", _iso(AS_OF - pd.Timedelta(days=200)), _iso(AS_OF - pd.Timedelta(days=200)), people=["Bob"]),
        _event(3, "Dinner", _iso(AS_OF + pd.Timedelta(days=3)), _iso(AS_OF + pd.Timedelta(days=3)), location=SOME_VENUE),
        _event(4, "Party", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, top_n=1, seed=0)
    people_by_event = dict(zip(result["event_title"], result["person"]))
    assert set(people_by_event.values()) == {"Alice", "Bob"}
    assert people_by_event["Dinner"] != people_by_event["Party"]  # not the same person for both


def test_upcoming_invite_suggestions_picks_are_random_across_calls():
    # With more eligible people than top_n, an unseeded call should be
    # capable of returning different picks from one call to the next -
    # this is what backs the dashboard's Refresh button.
    events = [
        _event(i, "Coffee", _iso(AS_OF - pd.Timedelta(days=400 + i)), _iso(AS_OF - pd.Timedelta(days=400 + i)),
               people=[f"Person{i}"])
        for i in range(20)
    ] + [_event(99, "Dinner", _iso(AS_OF + pd.Timedelta(days=3)), _iso(AS_OF + pd.Timedelta(days=3)), location=SOME_VENUE)]
    df = ingest.load_events_from_records(events)
    picks = {
        tuple(sorted(reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, top_n=5)["person"]))
        for _ in range(20)
    }
    assert len(picks) > 1  # not the same 5 people every single time


def test_upcoming_invite_suggestions_excludes_someone_with_no_known_region():
    # Inga's only event with you had no real location on it (a Zoom call,
    # here) - she never established a usual region with you anywhere, so
    # she's excluded from a region-matched upcoming event on that basis
    # alone, the same as anyone else with no located shared history. This
    # falls straight out of the region-matching rule already in place
    # (person_region.get(p) == region) - no separate "was this virtual"
    # check needed.
    events = [
        _event(1, "Errand", _iso(WITHIN_CAP), _iso(WITHIN_CAP), location="Cafe A"),
        _event(2, "Catchup", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Inga"],
               category="Important", location="https://zoom.us/j/123456"),
        _event(3, "Wedding", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               category="Important", location="Cafe A"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF, seed=0)
    assert "Inga" not in result["person"].values


def test_upcoming_invite_suggestions_guesses_region_from_address_text():
    events = [
        # A past, in-person event at a geocoded LA venue - establishes
        # "Los Angeles, United States" as a known metro.
        _event(1, "Errand", _iso(WITHIN_CAP), _iso(WITHIN_CAP), location="Cafe A"),
        # Alice: always hangs out at a *different*, ungeocoded LA address.
        _event(2, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Alice"],
               location="1903 Hyperion Ave Los Angeles, CA"),
        # Erin: always hangs out at a geocoded Bay Area venue.
        _event(3, "Coffee", _iso(WITHIN_CAP), _iso(WITHIN_CAP), people=["Erin"], location="Cafe B"),
        # An upcoming dinner at yet another ungeocoded LA address whose
        # text names the same city.
        _event(4, "Dinner", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)),
               location="500 Sunset Blvd Los Angeles, CA"),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=GEO_CACHE, as_of=AS_OF, seed=0)
    assert "Alice" in result["person"].values  # guessed as LA from the address text
    assert "Erin" not in result["person"].values  # known Bay Area, excluded
    row = result[result["person"] == "Alice"].iloc[0]
    assert row["region"] == "Los Angeles, United States"


def test_upcoming_invite_suggestions_keeps_same_start_events_grouped_separately():
    # Two different upcoming events (different categories, so their
    # candidate pools don't overlap) land on the exact same start time -
    # each should still get its own contiguous block of rows rather than
    # interleaving.
    events = [
        _event(1, "History1", _iso(AS_OF - pd.Timedelta(days=700)), _iso(AS_OF - pd.Timedelta(days=700)), people=["P1"], category="Work"),
        _event(2, "History2", _iso(AS_OF - pd.Timedelta(days=100)), _iso(AS_OF - pd.Timedelta(days=100)), people=["P2"], category="Work"),
        _event(3, "History3", _iso(AS_OF - pd.Timedelta(days=650)), _iso(AS_OF - pd.Timedelta(days=650)), people=["P3"], category="Fun"),
        _event(4, "History4", _iso(AS_OF - pd.Timedelta(days=50)), _iso(AS_OF - pd.Timedelta(days=50)), people=["P4"], category="Fun"),
        _event(5, "Alpha", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Work", location=SOME_VENUE),
        _event(6, "Beta", _iso(AS_OF + pd.Timedelta(days=5)), _iso(AS_OF + pd.Timedelta(days=5)), category="Fun", location=SOME_VENUE),
    ]
    df = ingest.load_events_from_records(events)
    result = reconnect.upcoming_invite_suggestions(df, geocode_cache=None, as_of=AS_OF, top_n=2, seed=0)
    titles = result["event_title"].tolist()
    assert set(titles) == {"Alpha", "Beta"}
    for title in set(titles):
        indices = [i for i, t in enumerate(titles) if t == title]
        assert indices == list(range(indices[0], indices[0] + len(indices))), \
            f"{title}'s rows aren't contiguous: {titles}"
