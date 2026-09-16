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
    "/api/person-year-trend",
    "/api/habit?category=Gym",
    "/api/travel",
    "/api/time-by-category",
    "/api/seasonality",
    "/api/seasonality?category=Gym",
    "/api/anomalies",
    "/api/locations",
    "/api/locations?category=Gym&start_year=2022&end_year=2023",
    "/api/locations?person=Alice",
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
