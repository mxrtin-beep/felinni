"""Unit tests for felinni.ingest's data-cleaning: junk-location filtering,
title-based people parsing, and all-day exclusion downstream in spending."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import anomalies, ingest, seasonality, spending, travel


def _event(idx, title, start="2024-01-01T19:00:00Z", end="2024-01-01T21:00:00Z", location=None,
           attendees=None, is_all_day=False, category=None, calendar="Social", calendar_color=None):
    notes = f"Category: {category}" if category else None
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": notes,
        "location": location,
        "startDate": start,
        "endDate": end,
        "isAllDay": is_all_day,
        "calendarTitle": calendar,
        "calendarColorHex": calendar_color,
        "attendees": attendees or [],
        "isRecurring": False,
        "url": None,
        "noteTags": ({"category": [category]} if category else {}),
    }


def _load(tmp_path, events):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(events))
    return ingest.load_events(path)


@pytest.mark.parametrize("junk_location", [
    "https://zoom.us/j/1234567890",
    "https://us02web.zoom.us/j/1234567890?pwd=abc",
    "+1 415-555-0100",
    "(415) 555-0100",
    "https://meet.google.com/abc-defg-hij",
])
def test_junk_locations_are_filtered_out(tmp_path, junk_location):
    df = _load(tmp_path, [_event(1, "Standup", location=junk_location)])
    assert df.iloc[0]["location"] is None


def test_real_locations_are_kept(tmp_path):
    df = _load(tmp_path, [_event(1, "Dinner", location="Nopa, San Francisco")])
    assert df.iloc[0]["location"] == "Nopa, San Francisco"


@pytest.mark.parametrize("title,expected", [
    ("Dinner with John Doe, Jane Doe, and McLovin", ["Jane Doe", "John Doe", "McLovin"]),
    ("Coffee with Alice", ["Alice"]),
    ("Drinks with Bob and Carla", ["Bob", "Carla"]),
])
def test_people_parsed_from_title_when_untagged(tmp_path, title, expected):
    df = _load(tmp_path, [_event(1, title)])
    assert sorted(df.iloc[0]["people"]) == sorted(expected)


def test_title_parsing_skipped_when_not_name_like(tmp_path):
    df = _load(tmp_path, [_event(1, "Lunch with the whole team")])
    assert df.iloc[0]["people"] == []


def test_title_parsed_people_merge_with_existing_attendees(tmp_path):
    # A group event where only one friend was formally invited in Calendar
    # and the rest are just named in the title should keep everyone, not
    # just the formal attendee.
    df = _load(tmp_path, [_event(1, "Dinner with John Doe", attendees=["Someone Else"])])
    assert sorted(df.iloc[0]["people"]) == ["John Doe", "Someone Else"]


def test_group_title_keeps_valid_names_even_if_one_token_is_not_name_like(tmp_path):
    df = _load(tmp_path, [_event(1, "Game night with Alice, Bob, Carla, and the twins")])
    assert sorted(df.iloc[0]["people"]) == ["Alice", "Bob", "Carla"]


def test_curly_apostrophe_in_name_is_recognized(tmp_path):
    df = _load(tmp_path, [_event(1, "Drinks with Sean O’Brien")])
    assert df.iloc[0]["people"] == ["Sean O’Brien"]


def test_trailing_emoji_does_not_break_group_parsing(tmp_path):
    df = _load(tmp_path, [_event(1, "Dinner with Alice, Bob, and Carla \U0001F389")])
    assert sorted(df.iloc[0]["people"]) == ["Alice", "Bob", "Carla"]


def test_bare_first_name_in_group_event_gets_most_common_last_name(tmp_path):
    events = [
        _event(1, "Dinner with Alice Smith"),
        _event(2, "Dinner with Alice Smith"),
        _event(3, "Coffee with Alice Rodriguez"),
        # Group event tagged casually with just a first name.
        _event(4, "Game night with Alice, Bob"),
    ]
    df = _load(tmp_path, events)
    game_night = df[df["title"] == "Game night with Alice, Bob"].iloc[0]
    assert sorted(game_night["people"]) == ["Alice Smith", "Bob"]


def test_bare_first_name_in_solo_event_is_left_alone(tmp_path):
    events = [
        _event(1, "Dinner with Alice Smith"),
        _event(2, "Dinner with Alice Smith"),
        # A 1:1 event tagged with just the first name - not enough context
        # to assume it's the same "Alice Smith", so left as-is.
        _event(3, "Coffee with Alice"),
    ]
    df = _load(tmp_path, events)
    coffee = df[df["title"] == "Coffee with Alice"].iloc[0]
    assert coffee["people"] == ["Alice"]


def test_first_name_resolution_dedupes_when_already_tagged_in_full(tmp_path):
    events = [
        _event(1, "Dinner with Alice Smith"),
        _event(2, "Dinner with Alice Smith"),
        _event(3, "Game night with Alice, Alice Smith, Bob"),
    ]
    df = _load(tmp_path, events)
    game_night = df[df["title"] == "Game night with Alice, Alice Smith, Bob"].iloc[0]
    assert sorted(game_night["people"]) == ["Alice Smith", "Bob"]


def test_time_by_category_excludes_all_day_events(tmp_path):
    events = [
        _event(1, "Vacation", location=None, is_all_day=True, category="Travel", calendar="Travel"),
        _event(2, "Gym", category="Gym", calendar="Gym"),
    ]
    df = _load(tmp_path, events)
    result = spending.time_by_category(df)
    assert "Travel" not in result.index
    assert "Gym" in result.index


def test_seasonality_excludes_all_day_events(tmp_path):
    events = [
        _event(1, "Birthday", is_all_day=True, category="Personal", calendar="Personal",
               start="2024-06-01T00:00:00Z", end="2024-06-02T00:00:00Z"),
        _event(2, "Gym", category="Gym", calendar="Gym", start="2024-06-01T07:00:00Z", end="2024-06-01T08:00:00Z"),
    ]
    df = _load(tmp_path, events)
    monthly = seasonality.monthly_activity(df)
    assert monthly.loc[6, "avg_events_per_month"] == 1  # only the Gym event counts


def test_anomalies_excludes_all_day_events(tmp_path):
    events = [
        _event(1, "Company offsite", is_all_day=True, category="Work", calendar="Work",
               start="2024-06-01T00:00:00Z", end="2024-06-02T00:00:00Z"),
        _event(2, "Gym", category="Gym", calendar="Gym", start="2024-06-01T07:00:00Z", end="2024-06-01T08:00:00Z"),
    ]
    df = _load(tmp_path, events)
    load = anomalies.weekly_load(df)
    assert load["total_hours"].sum() == 1.0  # the 24h all-day event isn't counted


def test_travel_falls_back_to_title_keyword_match(tmp_path):
    events = [_event(1, "Flight to Tokyo", category=None, calendar="Personal", location="Tokyo, Japan")]
    df = _load(tmp_path, events)
    matched = travel.travel_events(df)
    assert len(matched) == 1


def test_category_color_map_uses_calendar_color(tmp_path):
    events = [
        _event(1, "Gym", calendar="Gym", calendar_color="#FF0000"),
        _event(2, "Gym", calendar="Gym", calendar_color="#FF0000"),
        _event(3, "Social", calendar="Social", calendar_color=None),
    ]
    df = _load(tmp_path, events)
    colors = ingest.category_color_map(df)
    assert colors == {"Gym": "#FF0000"}


def test_category_color_map_empty_when_no_colors_captured(tmp_path):
    events = [_event(1, "Gym", calendar="Gym")]
    df = _load(tmp_path, events)
    assert ingest.category_color_map(df) == {}
