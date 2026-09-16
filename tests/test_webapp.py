"""Smoke tests for the dashboard's Flask API against the synthetic fixture."""
import sys
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
    for path in ["/", "/app.js", "/charts.js", "/styles.css"]:
        resp = client.get(path)
        assert resp.status_code == 200, path
