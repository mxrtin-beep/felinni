"""Tests for felinni.geocode's location-anchor logic (the UCLA-building
case): a bare building/room name whose category matches a configured
anchor gets that anchor appended to the geocoding QUERY only, never to
the cached/displayed location string."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import geocode


def _fake_result(lat=34.07, lon=-118.44, city=None, country=None):
    result = MagicMock()
    result.latitude = lat
    result.longitude = lon
    result.address = "fake address"
    address = {}
    if city:
        address["city"] = city
    if country:
        address["country"] = country
    result.raw = {"address": address}
    return result


def test_anchor_appended_to_query_not_to_cache_key(tmp_path):
    cache_path = tmp_path / "cache.json"
    queries_seen = []

    def fake_geocode(query, **kwargs):
        queries_seen.append(query)
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = fake_geocode

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["North Campus Student Center"],
            cache_path=cache_path,
            rate_limit_seconds=0,
            location_categories={"North Campus Student Center": "UCLA Clubs"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert queries_seen == ["North Campus Student Center, UCLA, Los Angeles, CA"]
    # cached and returned under the *original* location string, unchanged
    assert "North Campus Student Center" in result
    assert result["North Campus Student Center"]["lat"] == 34.07


def test_no_anchor_when_category_does_not_match(tmp_path):
    cache_path = tmp_path / "cache.json"
    queries_seen = []
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: queries_seen.append(q) or _fake_result()

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Nopa"],
            cache_path=cache_path,
            rate_limit_seconds=0,
            location_categories={"Nopa": "Social"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert queries_seen == ["Nopa"]


def test_failed_geocode_is_retried_on_next_run(tmp_path):
    cache_path = tmp_path / "cache.json"
    call_count = {"n": 0}

    def flaky_then_success(query, **kwargs):
        call_count["n"] += 1
        return None if call_count["n"] == 1 else _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = flaky_then_success

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        first = geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, rate_limit_seconds=0)
        assert first["Mono Lake"] is None  # cached as a failure...

        second = geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, rate_limit_seconds=0)
        # ...but retried (not treated as permanently resolved) and now succeeds.
        assert second["Mono Lake"] is not None
        assert call_count["n"] == 2


def test_successful_geocode_captures_structured_city_and_country(tmp_path):
    cache_path = tmp_path / "cache.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result(city="Santa Monica", country="United States")

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(["Santa Monica Pier"], cache_path=cache_path, rate_limit_seconds=0)

    assert result["Santa Monica Pier"]["city"] == "Santa Monica"
    assert result["Santa Monica Pier"]["country"] == "United States"


def test_overrides_win_over_cache_and_skip_network(tmp_path):
    cache_path = tmp_path / "cache.json"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({
        "Santa Monica Pier": {"lat": 34.0094, "lon": -118.4973, "display_name": "override"},
    }))
    # Cache pre-seeded with a wrong answer, as if from a bad earlier run.
    cache_path.write_text(json.dumps({"Santa Monica Pier": {"lat": 48.0, "lon": 2.0, "display_name": "wrong"}}))

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = AssertionError("should never be called for an overridden location")

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["Santa Monica Pier"], cache_path=cache_path, overrides_path=overrides_path, rate_limit_seconds=0,
        )

    assert result["Santa Monica Pier"]["lat"] == 34.0094


def test_save_override_then_effective_cache_reflects_it(tmp_path):
    cache_path = tmp_path / "cache.json"
    overrides_path = tmp_path / "overrides.json"
    cache_path.write_text(json.dumps({"Royce 160": {"lat": 37.3, "lon": -120.5, "display_name": "wrong, Merced"}}))

    geocode.save_override(
        "Royce 160", {"lat": 34.0722, "lon": -118.4441, "display_name": "Royce Hall, UCLA"},
        overrides_path=overrides_path,
    )

    effective = geocode.effective_cache(cache_path=cache_path, overrides_path=overrides_path)
    assert effective["Royce 160"]["lat"] == 34.0722
    assert effective["Royce 160"]["display_name"] == "Royce Hall, UCLA"


def test_save_override_rejects_non_numeric_coordinates(tmp_path):
    overrides_path = tmp_path / "overrides.json"
    import pytest
    with pytest.raises(ValueError):
        geocode.save_override("Somewhere", {"display_name": "no coords"}, overrides_path=overrides_path)


def test_geocode_one_returns_structured_result():
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result(city="Santa Monica", country="United States")
    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_one("Santa Monica Pier, Santa Monica, CA")
    assert result["city"] == "Santa Monica"
    assert result["lat"] == 34.07


def test_geocode_one_returns_none_when_not_found():
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: None
    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_one("nonsense query")
    assert result is None
