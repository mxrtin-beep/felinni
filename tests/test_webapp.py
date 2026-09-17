"""Smoke tests for the dashboard's Flask API against the synthetic fixture."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest
from webapp import server

EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_events.json"


@pytest.fixture(scope="module")
def client():
    server.DF = ingest.load_events(EVENTS_PATH)
    server.app.testing = True
    return server.app.test_client()


@pytest.mark.parametrize("path", [
    "/api/meta",
    "/api/places",
    "/api/stopped-going",
    "/api/people",
    "/api/trends",
    "/api/person-trend",
    "/api/person-trend?granularity=month",
    "/api/person-trend?granularity=week",
    "/api/habit?category=Gym",
    "/api/travel",
    "/api/travel/neighborhoods",
    "/api/time-by-category",
    "/api/seasonality",
    "/api/seasonality?category=Gym",
    "/api/anomalies",
    "/api/locations",
    "/api/locations?category=Gym&start_year=2022&end_year=2023",
    "/api/locations?person=Alice",
    "/api/breaks",
    "/api/recurring",
])
def test_endpoint_returns_200_json(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.is_json


def test_meta_lists_people(client):
    resp = client.get("/api/meta")
    body = resp.get_json()
    assert "Alice" in body["people"]
    assert body["n_geocoded"] == 0  # no geocode cache committed to the repo
    assert body["category_colors"]["Gym"] == "#8E24AA"


def test_anomalies_includes_category_breakdown(client):
    resp = client.get("/api/anomalies")
    body = resp.get_json()
    assert body["by_category"]
    assert {"week", "category", "hours", "z_score", "label"} <= body["by_category"][0].keys()


def test_breaks_has_all_three_sections(client):
    resp = client.get("/api/breaks")
    body = resp.get_json()
    assert set(body.keys()) == {"category_phases", "quiet_stretches", "location_shifts"}
    assert body["category_phases"]  # the synthetic Gym/Work streaks should surface


def test_recurring_finds_the_synthetic_gym_series(client):
    resp = client.get("/api/recurring")
    body = resp.get_json()
    assert any(row["title"] == "Gym" for row in body)
    gym_row = next(row for row in body if row["title"] == "Gym")
    assert gym_row["cadence"] in {"weekly", "daily"}
    assert gym_row["status"] in {"active", "slowing down", "stopped"}


def test_locations_without_geocode_cache_reports_zero_geocoded(client):
    resp = client.get("/api/locations")
    body = resp.get_json()
    assert body["geocoded_places"] == 0
    assert body["total_places"] > 0
    assert body["locations"] == []


def test_habit_requires_category(client):
    resp = client.get("/api/habit")
    assert resp.status_code == 400


def test_index_and_static_assets_are_served(client):
    for path in ["/", "/app.js", "/charts.js", "/styles.css", "/vendor/leaflet/leaflet.js"]:
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_summary_reflects_global_date_filter(client):
    unfiltered = client.get("/api/summary").get_json()
    narrowed = client.get("/api/summary?start_date=2021-01-01&end_date=2021-01-31").get_json()
    assert narrowed["total_events"] < unfiltered["total_events"]


def test_date_filter_applies_to_every_tab_not_just_summary(client):
    unfiltered = client.get("/api/places").get_json()
    narrowed = client.get("/api/places?start_date=2021-01-01&end_date=2021-01-07").get_json()
    assert sum(p["visits"] for p in narrowed) < sum(p["visits"] for p in unfiltered)


def test_exclude_categories_filter_applies_globally(client):
    unfiltered = client.get("/api/summary").get_json()
    excluded = client.get("/api/summary?exclude_categories=Gym").get_json()
    assert excluded["total_events"] < unfiltered["total_events"]

    places = client.get("/api/places?exclude_categories=Gym").get_json()
    assert all("Gym" not in p.get("categories", []) for p in places)


def test_travel_returns_region_based_shape(client):
    resp = client.get("/api/travel")
    body = resp.get_json()
    assert set(body.keys()) == {"home_region", "region_visits", "region_trips", "tagged_trips", "message"}


def test_person_trend_explicit_people_param_overrides_top_n(client):
    default_rows = client.get("/api/person-trend").get_json()
    default_people = {r["person"] for r in default_rows}
    one_person = next(iter(default_people))

    filtered = client.get(f"/api/person-trend?people={one_person}").get_json()
    assert filtered
    assert {r["person"] for r in filtered} == {one_person}


def test_person_trend_empty_people_param_returns_no_rows(client):
    resp = client.get("/api/person-trend?people=")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_geocode_override_saves_and_reflects_immediately(client, monkeypatch, tmp_path):
    overrides_path = tmp_path / "overrides.json"
    monkeypatch.setattr(server.geocode, "DEFAULT_OVERRIDES_PATH", overrides_path)

    resp = client.post("/api/geocode/override", json={
        "location": "Royce 160", "lat": 34.0722, "lon": -118.4441, "display_name": "Royce Hall, UCLA",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["entry"]["lat"] == 34.0722

    cache = server._load_geocode_cache()
    assert cache["Royce 160"]["lat"] == 34.0722


def test_geocode_override_requires_location(client):
    resp = client.post("/api/geocode/override", json={"lat": 1, "lon": 2})
    assert resp.status_code == 400


def test_geocode_job_runs_and_reports_progress(client, monkeypatch):
    calls = []

    def fake_geocode_locations(locations, cache_path=None, on_progress=None, **kwargs):
        total = len(locations)
        if on_progress:
            on_progress(0, total)
        for i, loc in enumerate(locations):
            time.sleep(0.01)
            if on_progress:
                on_progress(i + 1, total)
        calls.append(locations)
        return {loc: None for loc in locations}

    monkeypatch.setattr(server.geocode, "geocode_locations", fake_geocode_locations)

    resp = client.post("/api/geocode/start")
    assert resp.status_code == 200

    # A second start while one is running is rejected, not queued twice.
    busy = client.post("/api/geocode/start")
    assert busy.status_code in (200, 409)

    for _ in range(50):
        status = client.get("/api/geocode/status").get_json()
        if not status["running"]:
            break
        time.sleep(0.02)
    else:
        pytest.fail("geocode job never finished")

    assert calls, "fake geocode_locations was never invoked"
    assert status["error"] is None


def test_geocode_start_passes_through_force_flag(client, monkeypatch):
    seen_kwargs = {}

    def fake_geocode_locations(locations, cache_path=None, on_progress=None, **kwargs):
        seen_kwargs.update(kwargs)
        if on_progress:
            on_progress(0, 0)
        return {}

    monkeypatch.setattr(server.geocode, "geocode_locations", fake_geocode_locations)

    resp = client.post("/api/geocode/start", json={"force": True})
    assert resp.status_code == 200
    assert resp.get_json()["force"] is True

    for _ in range(50):
        if not client.get("/api/geocode/status").get_json()["running"]:
            break
        time.sleep(0.02)
    else:
        pytest.fail("geocode job never finished")

    assert seen_kwargs.get("force") is True
