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


def test_fading_or_growing_ignores_years_before_person_first_appears():
    # The calendar's own history spans 2015-2026 (via Carla, one event per
    # year), but Dana doesn't show up until 2022, ramps up through 2024,
    # then isn't seen at all in 2025 or 2026 - a real, recent fade. Fitting
    # against the calendar's full year range used to zero-pad 2015-2021
    # (years before Dana ever appears), which outweighed that drop-off and
    # reported this as "growing" - see analysis/felinni/social.py history.
    events = [
        _event(f"carla-{y}", "Standing coffee", f"{y}-06-01T09:00:00Z", f"{y}-06-01T10:00:00Z", people=["Carla"])
        for y in range(2015, 2027)
    ]
    idx = 0
    for year, n in [(2022, 1), (2023, 2), (2024, 4)]:
        for i in range(n):
            events.append(_event(
                f"dana-{idx}", "Hang out", f"{year}-{(i % 12) + 1:02d}-01T09:00:00Z",
                f"{year}-{(i % 12) + 1:02d}-01T10:00:00Z", people=["Dana"],
            ))
            idx += 1
    df = ingest.load_events_from_records(events)
    trend = social.fading_or_growing(df, min_total_events=1)
    dana = trend[trend["person"] == "Dana"].iloc[0]
    assert dana["first_year"] == 2022  # not 2015, the calendar's own start
    assert dana["last_year"] == 2026  # trailing silence is kept, not trimmed
    assert dana["slope_events_per_year"] < 0  # unseen in 2025-2026 outweighs 2022-2024 ramp


def test_friend_network_edges_links_co_attendees():
    events = [
        _event(1, "Dinner", "2024-01-01T19:00:00Z", "2024-01-01T21:00:00Z", people=["Alice", "Bob"]),
        _event(2, "Dinner", "2024-01-08T19:00:00Z", "2024-01-08T21:00:00Z", people=["Alice", "Bob"]),
        _event(3, "Coffee", "2024-01-10T09:00:00Z", "2024-01-10T10:00:00Z", people=["Alice"]),
    ]
    df = ingest.load_events_from_records(events)
    edges = social.friend_network_edges(df)
    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["person_a"], row["person_b"]} == {"Alice", "Bob"}
    assert row["shared_events"] == 2


def test_friend_network_edges_ignores_solo_events():
    events = [
        _event(1, "Coffee", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z", people=["Alice"]),
    ]
    df = ingest.load_events_from_records(events)
    assert social.friend_network_edges(df).empty


def test_friend_network_edges_ignores_all_day_events():
    """An all-day placeholder (a trip, a birthday) isn't "hanging out
    together" the way a timed event is - same exclusion as person_frequency."""
    events = [
        _event(1, "Trip", "2024-02-01T00:00:00Z", "2024-02-03T00:00:00Z",
               people=["Alice", "Bob"], is_all_day=True, category="Travel"),
    ]
    df = ingest.load_events_from_records(events)
    assert social.friend_network_edges(df).empty


def test_friend_network_edges_handles_three_way_events():
    """A three-person event should produce all three pairs."""
    events = [
        _event(1, "Group hang", "2024-01-01T19:00:00Z", "2024-01-01T21:00:00Z", people=["Alice", "Bob", "Carol"]),
    ]
    df = ingest.load_events_from_records(events)
    edges = social.friend_network_edges(df)
    pairs = {frozenset([row["person_a"], row["person_b"]]) for _, row in edges.iterrows()}
    assert pairs == {frozenset(["Alice", "Bob"]), frozenset(["Alice", "Carol"]), frozenset(["Bob", "Carol"])}
    assert all(row["shared_events"] == 1 for _, row in edges.iterrows())
