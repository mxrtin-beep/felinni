"""Tests for felinni.regions: geographic-region grouping for the Travel
tab, keyed off geocoded city/country fields rather than a Travel category
tag."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, regions


def _event(idx, title, start, location, category="Social"):
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": f"Category: {category}",
        "location": location,
        "startDate": start,
        "endDate": start,
        "isAllDay": False,
        "calendarTitle": "Social",
        "calendarColorHex": None,
        "attendees": [],
        "isRecurring": False,
        "url": None,
        "noteTags": {"category": [category]},
    }


def _load(tmp_path, events):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(events))
    return ingest.load_events(path)


def test_region_for_uses_structured_city_and_country():
    entry = {"lat": 37.77, "lon": -122.43, "city": "San Francisco", "country": "United States",
             "display_name": "Tartine Bakery, 600 Guerrero St, San Francisco, California, United States"}
    assert regions.region_for(entry) == "San Francisco, United States"


def test_region_for_falls_back_to_display_name_parsing_for_older_entries():
    # No city/country fields - as if geocoded before that was captured.
    entry = {"lat": 37.77, "lon": -122.43,
             "display_name": "Tartine Bakery, 600 Guerrero St, San Francisco, California, 94110, United States"}
    assert regions.region_for(entry) == "San Francisco, United States"


def test_region_for_none_entry():
    assert regions.region_for(None) is None
    assert regions.region_for({}) is None


def _coords():
    return {
        "Gym A": {"lat": 34.05, "lon": -118.25, "city": "Los Angeles", "country": "United States",
                  "display_name": "Gym A, Los Angeles, California, United States"},
        "Gym B": {"lat": 34.06, "lon": -118.26, "city": "Los Angeles", "country": "United States",
                  "display_name": "Gym B, Los Angeles, California, United States"},
        "Tokyo Hotel": {"lat": 35.68, "lon": 139.65, "city": "Tokyo", "country": "Japan",
                        "display_name": "Tokyo Hotel, Tokyo, Japan"},
    }


def test_visits_by_region_and_home_region(tmp_path):
    events = [
        _event(1, "Gym", "2024-01-01T07:00:00Z", "Gym A"),
        _event(2, "Gym", "2024-01-03T07:00:00Z", "Gym B"),
        _event(3, "Gym", "2024-01-05T07:00:00Z", "Gym A"),
        _event(4, "Trip", "2024-06-01T10:00:00Z", "Tokyo Hotel"),
    ]
    df = _load(tmp_path, events)
    coords = _coords()
    visits = regions.visits_by_region(df, coords)
    assert "Los Angeles, United States" in visits.index
    assert "Tokyo, Japan" in visits.index
    assert regions.home_region(visits) == "Los Angeles, United States"


def test_trips_away_from_home_excludes_home_region(tmp_path):
    events = [
        _event(1, "Gym", "2024-01-01T07:00:00Z", "Gym A"),
        _event(2, "Gym", "2024-01-03T07:00:00Z", "Gym B"),
        _event(3, "Gym", "2024-01-05T07:00:00Z", "Gym A"),
        _event(4, "Trip day 1", "2024-06-01T10:00:00Z", "Tokyo Hotel"),
        _event(5, "Trip day 2", "2024-06-02T10:00:00Z", "Tokyo Hotel"),
    ]
    df = _load(tmp_path, events)
    trips = regions.trips_away_from_home(df, _coords())
    assert len(trips) == 1
    assert trips.iloc[0]["region"] == "Tokyo, Japan"
    assert trips.iloc[0]["n_events"] == 2


def test_trips_away_from_home_empty_without_geocoding(tmp_path):
    events = [_event(1, "Gym", "2024-01-01T07:00:00Z", "Gym A")]
    df = _load(tmp_path, events)
    trips = regions.trips_away_from_home(df, {})
    assert trips.empty
