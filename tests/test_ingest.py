"""Unit tests for felinni.ingest's data-cleaning: junk-location filtering,
title-based people parsing, and all-day exclusion downstream in spending."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import anomalies, ingest, seasonality, spending, travel


def _event(idx, title, start="2024-01-01T19:00:00Z", end="2024-01-01T21:00:00Z", location=None,
           attendees=None, is_all_day=False, category=None, calendar="Social"):
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


def test_title_parsing_skipped_when_attendees_already_tagged(tmp_path):
    df = _load(tmp_path, [_event(1, "Dinner with John Doe", attendees=["Someone Else"])])
    assert df.iloc[0]["people"] == ["Someone Else"]


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
