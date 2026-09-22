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


def _metro_coords():
    return {
        # Downtown LA and Santa Monica are ~25km apart - one metro.
        "Downtown Office": {"lat": 34.0522, "lon": -118.2437, "city": "Los Angeles", "country": "United States",
                             "neighbourhood": "Downtown", "display_name": "Downtown Office, Los Angeles"},
        "Beach House": {"lat": 34.0195, "lon": -118.4912, "city": "Santa Monica", "country": "United States",
                        "neighbourhood": "Santa Monica", "display_name": "Beach House, Santa Monica"},
        # San Diego is ~180km from LA - a distinct metro.
        "SD Hotel": {"lat": 32.7157, "lon": -117.1611, "city": "San Diego", "country": "United States",
                     "display_name": "SD Hotel, San Diego"},
    }


def test_nearby_cities_are_grouped_into_one_metro(tmp_path):
    events = [
        _event(1, "Work", "2024-01-01T09:00:00Z", "Downtown Office"),
        _event(2, "Work", "2024-01-02T09:00:00Z", "Downtown Office"),
        _event(3, "Beach", "2024-01-06T09:00:00Z", "Beach House"),
    ]
    df = _load(tmp_path, events)
    visits = regions.visits_by_region(df, _metro_coords())
    # One combined metro (labeled after the higher-visit city), not two.
    assert len(visits) == 1
    assert visits.index[0] == "Los Angeles, United States"
    assert visits.iloc[0]["visits"] == 3


def test_distant_city_is_a_separate_metro(tmp_path):
    events = [
        _event(1, "Work", "2024-01-01T09:00:00Z", "Downtown Office"),
        _event(2, "Trip", "2024-06-01T09:00:00Z", "SD Hotel"),
    ]
    df = _load(tmp_path, events)
    visits = regions.visits_by_region(df, _metro_coords())
    assert set(visits.index) == {"Los Angeles, United States", "San Diego, United States"}


def test_metro_area_names_override_applies_friendly_label(tmp_path, monkeypatch):
    monkeypatch.setitem(regions.METRO_AREA_NAMES, "Los Angeles, United States", "Greater LA")
    events = [_event(1, "Work", "2024-01-01T09:00:00Z", "Downtown Office")]
    df = _load(tmp_path, events)
    visits = regions.visits_by_region(df, _metro_coords())
    assert "Greater LA" in visits.index


def _dc_area_coords():
    return {
        # National Harbor: one address, but a weekly recurring event there
        # racks up far more individual visits than any single DC address.
        "Weekly Meetup Spot": {"lat": 38.7823, "lon": -77.0166, "city": "National Harbor", "country": "United States",
                                "display_name": "Weekly Meetup Spot, National Harbor, Maryland, United States"},
        # DC proper: several different one-off addresses, each visited
        # less often, but genuinely more of the metro's distinct places.
        "Museum": {"lat": 38.8913, "lon": -77.0200, "city": "Washington", "country": "United States",
                   "display_name": "Museum, Washington, DC, United States"},
        "Friend's Apartment": {"lat": 38.9072, "lon": -77.0369, "city": "Washington", "country": "United States",
                               "display_name": "Friend's Apartment, Washington, DC, United States"},
        "Restaurant": {"lat": 38.9047, "lon": -77.0163, "city": "Washington", "country": "United States",
                       "display_name": "Restaurant, Washington, DC, United States"},
    }


def test_metro_label_prefers_the_city_with_more_distinct_places_over_one_frequent_address(tmp_path):
    # A real reported case: a single recurring event at one National
    # Harbor venue outnumbered DC in raw visits, so the whole metro got
    # labeled "National Harbor" - not what most of the actual geography
    # of the trip was. The city covering more distinct locations (DC, 3
    # addresses) should win over one single frequently-visited address
    # (National Harbor, 1 address) even though it has fewer raw visits.
    events = [_event(i, "Meetup", f"2024-01-{i:02d}T09:00:00Z", "Weekly Meetup Spot") for i in range(1, 6)]
    events += [
        _event(10, "Museum visit", "2024-02-01T09:00:00Z", "Museum"),
        _event(11, "Dinner", "2024-02-02T09:00:00Z", "Friend's Apartment"),
        _event(12, "Lunch", "2024-02-03T09:00:00Z", "Restaurant"),
    ]
    df = _load(tmp_path, events)
    coords = _dc_area_coords()
    visits = regions.visits_by_region(df, coords)
    assert len(visits) == 1  # National Harbor is within METRO_AREA_RADIUS_KM of DC
    assert visits.index[0] == "Washington, United States"


def test_neighborhoods_for_metro_uses_neighbourhood_field(tmp_path):
    events = [
        _event(1, "Work", "2024-01-01T09:00:00Z", "Downtown Office"),
        _event(2, "Work", "2024-01-02T09:00:00Z", "Downtown Office"),
        _event(3, "Beach", "2024-01-06T09:00:00Z", "Beach House"),
    ]
    df = _load(tmp_path, events)
    neighborhoods = regions.neighborhoods_for_metro(df, _metro_coords(), "Los Angeles, United States")
    assert set(neighborhoods.index) == {"Downtown", "Santa Monica"}
    assert neighborhoods.loc["Downtown", "visits"] == 2


def test_neighborhoods_for_metro_empty_for_unknown_metro(tmp_path):
    events = [_event(1, "Work", "2024-01-01T09:00:00Z", "Downtown Office")]
    df = _load(tmp_path, events)
    neighborhoods = regions.neighborhoods_for_metro(df, _metro_coords(), "Nowhere")
    assert neighborhoods.empty
