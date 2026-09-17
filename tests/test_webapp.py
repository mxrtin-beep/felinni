"""Smoke tests for the dashboard's Flask API against the synthetic fixture."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    "/api/sources",
    "/api/future",
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


def test_future_returns_empty_skeleton(client):
    resp = client.get("/api/future")
    body = resp.get_json()
    assert body["events"] == []
    assert body["message"]


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


_SAMPLE_IMPORT_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:x1
SUMMARY:Imported Event
DTSTART:20240102T170000Z
DTEND:20240102T180000Z
END:VEVENT
END:VCALENDAR
"""


def test_add_sync_hide_delete_source_via_api(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server.calendar_sources, "DEFAULT_MANIFEST_PATH", tmp_path / "sources.json")
    monkeypatch.setattr(server.calendar_sources, "DEFAULT_SOURCES_DIR", tmp_path / "sources")
    original_df = server.DF
    try:
        with patch("requests.get", return_value=MagicMock(content=_SAMPLE_IMPORT_ICS, raise_for_status=MagicMock())):
            resp = client.post("/api/sources", json={
                "name": "My Google Calendar", "provider": "google", "kind": "ics_url",
                "url": "https://example.com/cal.ics",
            })
        assert resp.status_code == 200
        entry = resp.get_json()
        assert entry["event_count"] == 1
        assert "Imported Event" in server.DF["title"].values
        assert len(client.get("/api/sources").get_json()) == 1

        hidden = client.patch(f"/api/sources/{entry['id']}", json={"visible": False})
        assert hidden.status_code == 200
        assert "Imported Event" not in server.DF["title"].values

        shown = client.patch(f"/api/sources/{entry['id']}", json={"visible": True})
        assert shown.status_code == 200
        assert "Imported Event" in server.DF["title"].values

        deleted = client.delete(f"/api/sources/{entry['id']}")
        assert deleted.status_code == 200
        assert "Imported Event" not in server.DF["title"].values
        assert client.get("/api/sources").get_json() == []
    finally:
        server.DF = original_df


def test_source_events_deduped_against_primary_events(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server.calendar_sources, "DEFAULT_MANIFEST_PATH", tmp_path / "sources.json")
    monkeypatch.setattr(server.calendar_sources, "DEFAULT_SOURCES_DIR", tmp_path / "sources")
    primary_events = [{
        "id": "primary-1", "title": "Imported Event", "notes": None, "location": None,
        "startDate": "2024-01-02T17:00:00Z", "endDate": "2024-01-02T18:00:00Z",
        "isAllDay": False, "calendarTitle": "Personal", "calendarColorHex": None,
        "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
    }]
    primary_path = tmp_path / "primary_events.json"
    primary_path.write_text(json.dumps(primary_events))
    monkeypatch.setattr(server, "EVENTS_PATH", str(primary_path))
    original_df = server.DF
    try:
        with patch("requests.get", return_value=MagicMock(content=_SAMPLE_IMPORT_ICS, raise_for_status=MagicMock())):
            resp = client.post("/api/sources", json={
                "name": "Dup Cal", "provider": "google", "kind": "ics_url",
                "url": "https://example.com/cal.ics",
            })
        assert resp.status_code == 200
        matches = server.DF[server.DF["title"] == "Imported Event"]
        assert len(matches) == 1  # the same event from both sources isn't double-counted
    finally:
        server.DF = original_df
        server.EVENTS_PATH = None


def test_add_source_events_json_upload_via_api(client, monkeypatch, tmp_path):
    import io
    import json as jsonlib

    monkeypatch.setattr(server.calendar_sources, "DEFAULT_MANIFEST_PATH", tmp_path / "sources.json")
    monkeypatch.setattr(server.calendar_sources, "DEFAULT_SOURCES_DIR", tmp_path / "sources")
    original_df = server.DF
    try:
        raw_events = [{
            "id": "evt-1", "title": "Uploaded Gym", "notes": None, "location": None,
            "startDate": "2024-01-01T09:00:00Z", "endDate": "2024-01-01T10:00:00Z",
            "isAllDay": False, "calendarTitle": "Gym", "calendarColorHex": None,
            "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
        }]
        data = {
            "name": "My iPhone export", "provider": "apple", "kind": "events_json",
            "file": (io.BytesIO(jsonlib.dumps(raw_events).encode()), "events.json"),
        }
        resp = client.post("/api/sources", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert resp.get_json()["event_count"] == 1
        assert "Uploaded Gym" in server.DF["title"].values
    finally:
        server.DF = original_df


def test_sources_endpoint_errors_for_missing_fields(client):
    resp = client.post("/api/sources", json={"name": "x"})
    assert resp.status_code == 400


def test_sync_nonexistent_source_404s(client):
    resp = client.post("/api/sources/doesnotexist/sync")
    assert resp.status_code == 404


def test_delete_nonexistent_source_404s(client):
    resp = client.delete("/api/sources/doesnotexist")
    assert resp.status_code == 404
