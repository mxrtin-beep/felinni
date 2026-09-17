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
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json",
            rate_limit_seconds=0,
            location_categories={"North Campus Student Center": "UCLA Clubs"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert queries_seen == ["North Campus Student Center, UCLA, Los Angeles, CA"]
    # cached and returned under the *original* location string, unchanged
    assert "North Campus Student Center" in result
    assert result["North Campus Student Center"]["lat"] == 34.07


def test_dict_anchor_bounds_the_search_to_its_radius(tmp_path):
    cache_path = tmp_path / "cache.json"
    seen_kwargs = {}

    def fake_geocode(query, **kwargs):
        seen_kwargs.update(kwargs)
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = fake_geocode

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Royce Hall"],
            cache_path=cache_path,
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json",
            rate_limit_seconds=0,
            location_categories={"Royce Hall": "UCLA Clubs"},
            anchors={"ucla": {"query": "UCLA, Los Angeles, CA", "lat": 34.0689, "lon": -118.4452, "radius_km": 3.0}},
        )

    assert seen_kwargs["bounded"] is True
    (lat_min, lon_min), (lat_max, lon_max) = seen_kwargs["viewbox"]
    assert lat_min < 34.0689 < lat_max
    assert lon_min < -118.4452 < lon_max
    # A ~3km radius is a small box, not the whole world.
    assert (lat_max - lat_min) < 0.2


def test_string_anchor_does_not_bound_the_search(tmp_path):
    cache_path = tmp_path / "cache.json"
    seen_kwargs = {}

    def fake_geocode(query, **kwargs):
        seen_kwargs.update(kwargs)
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = fake_geocode

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["North Campus Student Center"],
            cache_path=cache_path,
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json",
            rate_limit_seconds=0,
            location_categories={"North Campus Student Center": "UCLA Clubs"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert "bounded" not in seen_kwargs or seen_kwargs.get("bounded") is False


def test_no_anchor_when_category_does_not_match(tmp_path):
    cache_path = tmp_path / "cache.json"
    queries_seen = []
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: queries_seen.append(q) or _fake_result()

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Nopa"],
            cache_path=cache_path,
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json",
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
        first = geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)
        assert first["Mono Lake"] is None  # cached as a failure...

        second = geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)
        # ...but retried (not treated as permanently resolved) and now succeeds.
        assert second["Mono Lake"] is not None
        assert call_count["n"] == 2


def test_unwrapped_exception_does_not_abort_the_whole_batch(tmp_path):
    cache_path = tmp_path / "cache.json"
    calls = []

    def flaky(query, **kwargs):
        calls.append(query)
        if query == "Bad Location":
            raise ConnectionError("network hiccup")  # not a GeocoderServiceError
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = flaky

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["Bad Location", "Good Location"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
        )

    assert calls == ["Bad Location", "Good Location"]  # kept going past the failure
    assert result["Bad Location"] is None
    assert result["Good Location"] is not None


def test_successful_geocode_captures_structured_city_and_country(tmp_path):
    cache_path = tmp_path / "cache.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result(city="Santa Monica", country="United States")

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(["Santa Monica Pier"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)

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
            ["Santa Monica Pier"], cache_path=cache_path, overrides_path=overrides_path,
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
        )

    assert result["Santa Monica Pier"]["lat"] == 34.0094


def test_force_reruns_already_cached_locations(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"Nopa": {"lat": 1.0, "lon": 1.0, "display_name": "old"}}))
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result(lat=2.0, lon=2.0)

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(["Nopa"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0, force=True)

    assert result["Nopa"]["lat"] == 2.0
    fake_geolocator.geocode.assert_called()


def test_force_does_not_touch_overridden_locations(tmp_path):
    cache_path = tmp_path / "cache.json"
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(json.dumps({"Nopa": {"lat": 9.0, "lon": 9.0, "display_name": "pinned"}}))
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = AssertionError("overridden location should never be queried")

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["Nopa"], cache_path=cache_path, overrides_path=overrides_path,
            diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0, force=True,
        )

    assert result["Nopa"]["lat"] == 9.0


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


def test_geocode_locations_uses_a_generous_default_timeout(tmp_path):
    """geopy's own Nominatim default timeout is 1 second - far too short
    for real-world latency, and a location that times out is cached as an
    unresolved failure indistinguishable from "couldn't find this place at
    all" (see felinni.geocode's module docstring). A bulk run needs a much
    longer per-request timeout than geopy's default, or a slow (but
    otherwise perfectly geocodable) location silently drops out."""
    cache_path = tmp_path / "cache.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result()

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator) as mock_nominatim:
        geocode.geocode_locations(["Navy Beach, Lee Vining, CA"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)

    assert mock_nominatim.call_args.kwargs["timeout"] == geocode.DEFAULT_TIMEOUT_SECONDS
    assert mock_nominatim.call_args.kwargs["timeout"] > 1


def test_geocode_locations_timeout_is_overridable(tmp_path):
    cache_path = tmp_path / "cache.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result()

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator) as mock_nominatim:
        geocode.geocode_locations(["Nopa"], cache_path=cache_path, diagnostics_path=tmp_path / "diagnostics.json", approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0, timeout=30)

    assert mock_nominatim.call_args.kwargs["timeout"] == 30


def test_geocode_one_uses_a_generous_default_timeout():
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: _fake_result()
    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator) as mock_nominatim:
        geocode.geocode_one("Navy Beach, Lee Vining, CA")
    assert mock_nominatim.call_args.kwargs["timeout"] == geocode.DEFAULT_TIMEOUT_SECONDS


def test_diagnostics_records_no_match_reason(tmp_path):
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: None  # Nominatim ran fine, found nothing

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Boelter 5800"], cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
        )

    diagnostics = geocode.load_diagnostics(diagnostics_path)
    assert diagnostics["Boelter 5800"] == "Nominatim found no match for this query"


def test_diagnostics_distinguishes_timeout_from_service_error_and_network_error(tmp_path):
    from geopy.exc import GeocoderServiceError, GeocoderTimedOut

    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"

    def flaky(query, **kwargs):
        if query == "Timeout Place":
            raise GeocoderTimedOut("timed out")
        if query == "Blocked Place":
            raise GeocoderServiceError("Non-successful status code 403")
        raise ConnectionError("dns failure")

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = flaky

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Timeout Place", "Blocked Place", "Broken Place"],
            cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
        )

    diagnostics = geocode.load_diagnostics(diagnostics_path)
    assert "timed out" in diagnostics["Timeout Place"]
    assert "403" in diagnostics["Blocked Place"]
    assert "dns failure" in diagnostics["Broken Place"]


def test_diagnostics_includes_the_anchor_augmented_query_when_different(tmp_path):
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: None

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["North Campus Student Center"],
            cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
            location_categories={"North Campus Student Center": "UCLA Clubs"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    diagnostics = geocode.load_diagnostics(diagnostics_path)
    assert "North Campus Student Center, UCLA, Los Angeles, CA" in diagnostics["North Campus Student Center"]


def test_diagnostics_entry_cleared_once_a_location_succeeds(tmp_path):
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    call_count = {"n": 0}

    def flaky_then_success(query, **kwargs):
        call_count["n"] += 1
        return None if call_count["n"] == 1 else _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = flaky_then_success

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)
        assert "Mono Lake" in geocode.load_diagnostics(diagnostics_path)

        geocode.geocode_locations(["Mono Lake"], cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0)
        assert "Mono Lake" not in geocode.load_diagnostics(diagnostics_path)


def test_load_diagnostics_empty_when_no_file():
    assert geocode.load_diagnostics(Path("/nonexistent/diagnostics.json")) == {}


def test_clear_diagnostics_entry_removes_only_that_location(tmp_path):
    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics_path.write_text(json.dumps({"A": "reason A", "B": "reason B"}))

    geocode.clear_diagnostics_entry("A", diagnostics_path)

    remaining = geocode.load_diagnostics(diagnostics_path)
    assert remaining == {"B": "reason B"}


# --- Real failures a user hit: a UCLA-category address that already has its
# own city/state/ZIP getting a redundant, conflicting "UCLA, Los Angeles,
# CA" anchor appended, and a flattened multi-line address missing the comma
# between the street and city. ---

def test_insert_missing_city_comma_fixes_a_flattened_address():
    fixed = geocode._insert_missing_city_comma("11024 Strathmore Dr Los Angeles, CA, United States")
    assert fixed == "11024 Strathmore Dr, Los Angeles, CA, United States"


def test_insert_missing_city_comma_handles_multi_word_city():
    fixed = geocode._insert_missing_city_comma("1 Amgen Center Dr Newbury Park, CA, United States")
    assert fixed == "1 Amgen Center Dr, Newbury Park, CA, United States"


def test_insert_missing_city_comma_is_a_no_op_when_already_well_formed():
    already_fine = "Broad Art Center 240 Charles E Young Dr N, Los Angeles, CA 90095, United States"
    assert geocode._insert_missing_city_comma(already_fine) == already_fine


def test_looks_like_complete_address_true_for_zip_or_country():
    assert geocode._looks_like_complete_address("Covel Commons 200 De Neve Dr, Los Angeles, CA 90095, United States")
    assert geocode._looks_like_complete_address("1 Amgen Center Dr Newbury Park, CA, United States")


def test_looks_like_complete_address_false_for_a_bare_building_name():
    assert not geocode._looks_like_complete_address("North Campus Student Center")
    assert not geocode._looks_like_complete_address("Boelter 5800")


def test_anchor_skipped_for_an_address_that_already_has_its_own_city_state(tmp_path):
    """A UCLA-category event whose location is already a full mailing
    address shouldn't get ", UCLA, Los Angeles, CA" tacked onto the end -
    that redundant, conflicting context is what was breaking an otherwise
    perfectly resolvable address."""
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    queries_seen = []

    def fake_geocode(query, **kwargs):
        queries_seen.append(query)
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = fake_geocode

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Broad Art Center 240 Charles E Young Dr N, Los Angeles, CA 90095, United States"],
            cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
            location_categories={"Broad Art Center 240 Charles E Young Dr N, Los Angeles, CA 90095, United States": "UCLA Classes"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert queries_seen == ["Broad Art Center 240 Charles E Young Dr N, Los Angeles, CA 90095, United States"]


def test_unresolvable_anchored_location_falls_back_to_the_anchors_own_coordinates(tmp_path):
    """"Boelter 5800" is a UCLA room number, not its own addressable point -
    if even the anchored query ("Boelter 5800, UCLA, Los Angeles, CA")
    can't be resolved, it should land at UCLA's own coordinates rather than
    being left off the map entirely."""
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    approximations_path = tmp_path / "approximations.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: None  # never resolves, anchored or not

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["Boelter 5800"], cache_path=cache_path, diagnostics_path=diagnostics_path,
            approximations_path=approximations_path, rate_limit_seconds=0,
            location_categories={"Boelter 5800": "UCLA Classes"},
            anchors={"ucla": {"query": "UCLA, Los Angeles, CA", "lat": 34.0689, "lon": -118.4452, "radius_km": 3.0}},
        )

    assert result["Boelter 5800"]["lat"] == 34.0689
    assert result["Boelter 5800"]["lon"] == -118.4452
    assert "approximate" in result["Boelter 5800"]["display_name"]
    # Now has a usable pin, so it's not a "failure" - and it IS recorded as
    # an approximation, which is a distinct, non-alarming kind of note.
    assert "Boelter 5800" not in geocode.load_diagnostics(diagnostics_path)
    assert geocode.load_approximations(approximations_path)["Boelter 5800"] == "UCLA, Los Angeles, CA"


def test_string_anchor_has_no_coordinates_to_fall_back_to(tmp_path):
    """A plain-string anchor (just query-text context, e.g. "UCLA, Los
    Angeles, CA" appended with no known lat/lon of its own) can't provide a
    fallback pin - stays a real failure, same as before this feature."""
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    approximations_path = tmp_path / "approximations.json"
    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = lambda q, **kwargs: None

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        result = geocode.geocode_locations(
            ["Ackerman 2408"], cache_path=cache_path, diagnostics_path=diagnostics_path,
            approximations_path=approximations_path, rate_limit_seconds=0,
            location_categories={"Ackerman 2408": "UCLA Clubs"},
            anchors={"ucla": "UCLA, Los Angeles, CA"},
        )

    assert result["Ackerman 2408"] is None
    assert "Ackerman 2408" in geocode.load_diagnostics(diagnostics_path)
    assert geocode.load_approximations(approximations_path) == {}


def test_approximation_cleared_once_the_real_address_resolves(tmp_path):
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    approximations_path = tmp_path / "approximations.json"
    call_count = {"n": 0}

    def flaky_then_success(query, **kwargs):
        call_count["n"] += 1
        return None if call_count["n"] == 1 else _fake_result(lat=34.069, lon=-118.442)

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = flaky_then_success
    anchors = {"ucla": {"query": "UCLA, Los Angeles, CA", "lat": 34.0689, "lon": -118.4452, "radius_km": 3.0}}

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["Boelter 5800"], cache_path=cache_path, diagnostics_path=diagnostics_path,
            approximations_path=approximations_path, rate_limit_seconds=0,
            location_categories={"Boelter 5800": "UCLA Classes"}, anchors=anchors,
        )
        assert "Boelter 5800" in geocode.load_approximations(approximations_path)

        geocode.geocode_locations(
            ["Boelter 5800"], cache_path=cache_path, diagnostics_path=diagnostics_path,
            approximations_path=approximations_path, rate_limit_seconds=0,
            location_categories={"Boelter 5800": "UCLA Classes"}, anchors=anchors, force=True,
        )
        assert "Boelter 5800" not in geocode.load_approximations(approximations_path)


def test_clear_approximation_entry_removes_only_that_location(tmp_path):
    approximations_path = tmp_path / "approximations.json"
    approximations_path.write_text(json.dumps({"A": "UCLA, Los Angeles, CA", "B": "Amgen, Thousand Oaks, CA"}))

    geocode.clear_approximation_entry("A", approximations_path)

    assert geocode.load_approximations(approximations_path) == {"B": "Amgen, Thousand Oaks, CA"}


def test_load_approximations_empty_when_no_file():
    assert geocode.load_approximations(Path("/nonexistent/approximations.json")) == {}


def test_bounded_amgen_anchor_also_skipped_for_a_complete_address(tmp_path):
    cache_path = tmp_path / "cache.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    seen_kwargs = {}

    def fake_geocode(query, **kwargs):
        seen_kwargs.update(kwargs)
        return _fake_result()

    fake_geolocator = MagicMock()
    fake_geolocator.geocode.side_effect = fake_geocode

    with patch("geopy.geocoders.Nominatim", return_value=fake_geolocator):
        geocode.geocode_locations(
            ["1 Amgen Center Dr Newbury Park, CA, United States"],
            cache_path=cache_path, diagnostics_path=diagnostics_path, approximations_path=tmp_path / "approximations.json", rate_limit_seconds=0,
            location_categories={"1 Amgen Center Dr Newbury Park, CA, United States": "Work"},
            anchors={"amgen": {"query": "Amgen, Thousand Oaks, CA", "lat": 34.2064, "lon": -118.8253, "radius_km": 2.0}},
        )

    assert "bounded" not in seen_kwargs
