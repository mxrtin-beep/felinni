"""Tests for felinni.geocode's location-anchor logic (the UCLA-building
case): a bare building/room name whose category matches a configured
anchor gets that anchor appended to the geocoding QUERY only, never to
the cached/displayed location string."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import geocode


def _fake_result(lat=34.07, lon=-118.44):
    result = MagicMock()
    result.latitude = lat
    result.longitude = lon
    result.address = "fake address"
    return result


def test_anchor_appended_to_query_not_to_cache_key(tmp_path):
    cache_path = tmp_path / "cache.json"
    queries_seen = []

    def fake_geocode(query):
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
    fake_geolocator.geocode.side_effect = lambda q: queries_seen.append(q) or _fake_result()

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Nopa"],
            cache_path=cache_path,
            rate_limit_seconds=0,
            location_categories={"Nopa": "Social"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert queries_seen == ["Nopa"]
