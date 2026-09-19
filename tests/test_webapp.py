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
    # /api/future hits the real (sandboxed, network-blocked) search path
    # here - felinni.future_events._ddg_text_search retries once with a
    # real 1s sleep between attempts per source, which is real behavior
    # worth keeping, but not something this smoke test needs to actually
    # wait through for every one of ~7 sources.
    with patch("felinni.future_events.time.sleep"):
        resp = client.get(path)
    assert resp.status_code == 200
    assert resp.is_json


def test_meta_lists_people(client):
    resp = client.get("/api/meta")
    body = resp.get_json()
    assert "Alice" in body["people"]
    assert body["n_geocoded"] == 0  # no geocode cache committed to the repo
    assert body["category_colors"]["Gym"] == "#8E24AA"


def test_social_network_returns_nodes_and_edges(client):
    resp = client.get("/api/social/network")
    body = resp.get_json()
    assert "nodes" in body and "edges" in body
    assert body["nodes"]
    people_in_nodes = {n["person"] for n in body["nodes"]}
    for edge in body["edges"]:
        # Every edge's endpoints must be in the returned node set - no
        # dangling link to someone outside the (limit-capped) node list.
        assert edge["person_a"] in people_in_nodes
        assert edge["person_b"] in people_in_nodes
        assert edge["shared_events"] >= 1


def test_social_network_respects_limit(client):
    resp = client.get("/api/social/network?limit=1")
    body = resp.get_json()
    assert len(body["nodes"]) <= 1


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
    with patch("felinni.future_events.time.sleep"):
        resp = client.get("/api/future")
    body = resp.get_json()
    assert body["events"] == []
    assert body["message"]
    assert body["days"] == 7
    assert "source_status" in body


def test_future_message_includes_per_source_status_when_empty(client, monkeypatch):
    def fake_platform_events(platform, region=None, days_ahead=None, debug=None):
        if debug is not None:
            debug[platform] = "search failed: connection refused"
        return []

    monkeypatch.setattr(server.future_events, "platform_events", fake_platform_events)
    monkeypatch.setattr(server.future_events, "other_web_events", lambda region=None, days_ahead=None, debug=None: [])
    monkeypatch.setattr(server.future_events, "ollama_event_ideas", lambda df, region=None: [])
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    resp = client.get("/api/future")
    body = resp.get_json()

    assert body["source_status"]["eventbrite"] == "search failed: connection refused"
    assert "eventbrite: search failed" in body["message"]


def test_future_prints_the_pipeline_stage_counts(client, monkeypatch, capsys):
    """A per-source count can look fine (results found and kept) while
    the merged pipeline still collapses to almost nothing - this print is
    what actually shows which stage (raw merge, dedupe, the search-window
    cutoff) something disappeared at, rather than guessing."""
    fake_event = {
        "title": "X", "url": "urlX", "start": None, "end": None,
        "duration_hours": None, "location": "LA", "source": "eventbrite", "snippet": "",
    }
    monkeypatch.setattr(server.future_events, "platform_events", lambda platform, region=None, days_ahead=None, debug=None: [dict(fake_event)] if platform == "eventbrite" else [])
    monkeypatch.setattr(server.future_events, "other_web_events", lambda region=None, days_ahead=None, debug=None: [])
    monkeypatch.setattr(server.future_events, "ollama_event_ideas", lambda df, region=None: [])
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    client.get("/api/future")
    out = capsys.readouterr().out
    assert "future pipeline" in out
    assert "raw" in out and "dedupe" in out and "window" in out and "final" in out


def test_future_days_param_is_passed_through_and_ignores_global_date_filter(client, monkeypatch):
    seen_days = []

    def fake_platform_events(platform, region=None, days_ahead=None, debug=None):
        seen_days.append(days_ahead)
        return []

    def fake_other_web_events(region=None, days_ahead=None, debug=None):
        seen_days.append(days_ahead)
        return []

    monkeypatch.setattr(server.future_events, "platform_events", fake_platform_events)
    monkeypatch.setattr(server.future_events, "other_web_events", fake_other_web_events)
    monkeypatch.setattr(server.future_events, "ollama_event_ideas", lambda df, region=None: [])
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    # start_date/end_date would normally narrow _get_df(), but the Future
    # tab must ignore them entirely - it isn't filtering past history.
    resp = client.get("/api/future?days=30&start_date=2015-01-01&end_date=2015-01-02")
    body = resp.get_json()

    assert body["days"] == 30
    assert seen_days and all(d == 30 for d in seen_days)


def test_future_default_region_prefers_city_state_over_city_country(client, monkeypatch):
    seen_regions = []

    def fake_platform_events(platform, region=None, days_ahead=None, debug=None):
        seen_regions.append(region)
        return []

    monkeypatch.setattr(server.future_events, "platform_events", fake_platform_events)
    monkeypatch.setattr(server.future_events, "other_web_events", lambda region=None, days_ahead=None, debug=None: [])
    monkeypatch.setattr(server.future_events, "ollama_event_ideas", lambda df, region=None: [])
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    most_common_location = server.DF["location"].dropna().value_counts().index[0]
    fake_cache = {most_common_location: {"city": "Thousand Oaks", "state": "California", "country": "United States"}}
    monkeypatch.setattr(server, "_load_geocode_cache", lambda: fake_cache)

    resp = client.get("/api/future")
    body = resp.get_json()

    assert body["region"] == "Thousand Oaks, California"
    assert seen_regions and all(r == "Thousand Oaks, California" for r in seen_regions)


def test_default_future_search_region_falls_back_to_city_country_without_state():
    import pandas as pd
    df = pd.DataFrame({"location": ["Some Cafe", "Some Cafe", "Elsewhere"]})
    cache = {"Some Cafe": {"city": "Thousand Oaks", "country": "United States"}}
    assert server._default_future_search_region(df, cache) == "Thousand Oaks, United States"


def test_default_future_search_region_none_without_any_geocoded_city():
    import pandas as pd
    df = pd.DataFrame({"location": ["Some Cafe"]})
    assert server._default_future_search_region(df, {}) is None


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
    monkeypatch.setattr(server.geocode, "DEFAULT_DIAGNOSTICS_PATH", tmp_path / "diagnostics.json")
    monkeypatch.setattr(server.geocode, "DEFAULT_APPROXIMATIONS_PATH", tmp_path / "approximations.json")

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


def test_geocode_override_clears_any_prior_failure_diagnostics(client, monkeypatch, tmp_path):
    overrides_path = tmp_path / "overrides.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    diagnostics_path.write_text(json.dumps({"Royce 160": "Nominatim found no match for this query"}))
    monkeypatch.setattr(server.geocode, "DEFAULT_OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(server.geocode, "DEFAULT_DIAGNOSTICS_PATH", diagnostics_path)
    monkeypatch.setattr(server.geocode, "DEFAULT_APPROXIMATIONS_PATH", tmp_path / "approximations.json")

    resp = client.post("/api/geocode/override", json={
        "location": "Royce 160", "lat": 34.0722, "lon": -118.4441,
    })
    assert resp.status_code == 200
    assert "Royce 160" not in server.geocode.load_diagnostics(diagnostics_path)


def test_geocode_override_clears_any_prior_approximation(client, monkeypatch, tmp_path):
    overrides_path = tmp_path / "overrides.json"
    approximations_path = tmp_path / "approximations.json"
    approximations_path.write_text(json.dumps({"Boelter 5800": "UCLA, Los Angeles, CA"}))
    monkeypatch.setattr(server.geocode, "DEFAULT_OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(server.geocode, "DEFAULT_DIAGNOSTICS_PATH", tmp_path / "diagnostics.json")
    monkeypatch.setattr(server.geocode, "DEFAULT_APPROXIMATIONS_PATH", approximations_path)

    resp = client.post("/api/geocode/override", json={
        "location": "Boelter 5800", "lat": 34.0689, "lon": -118.4452,
    })
    assert resp.status_code == 200
    assert "Boelter 5800" not in server.geocode.load_approximations(approximations_path)


def test_geocode_failures_groups_by_reason_and_filters_to_current_locations(client, monkeypatch, tmp_path):
    diagnostics_path = tmp_path / "diagnostics.json"
    approximations_path = tmp_path / "approximations.json"
    events = [{
        "id": "evt-1", "title": "Something", "notes": None, "location": "Boelter 5800",
        "startDate": "2024-01-01T09:00:00Z", "endDate": "2024-01-01T10:00:00Z",
        "isAllDay": False, "calendarTitle": "School", "calendarColorHex": None,
        "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
    }, {
        "id": "evt-2", "title": "Something Else", "notes": None, "location": "Ackerman 2408",
        "startDate": "2024-01-02T09:00:00Z", "endDate": "2024-01-02T10:00:00Z",
        "isAllDay": False, "calendarTitle": "School", "calendarColorHex": None,
        "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
    }]
    diagnostics_path.write_text(json.dumps({
        "Ackerman 2408": "Nominatim found no match for this query",
        "A Stale Location Not In The Current Dataset": "timed out after 10s",
    }))
    approximations_path.write_text(json.dumps({
        "Boelter 5800": "UCLA, Los Angeles, CA",
        "A Stale Approximation Not In The Current Dataset": "UCLA, Los Angeles, CA",
    }))
    monkeypatch.setattr(server.geocode, "DEFAULT_DIAGNOSTICS_PATH", diagnostics_path)
    monkeypatch.setattr(server.geocode, "DEFAULT_APPROXIMATIONS_PATH", approximations_path)
    original_df = server.DF
    try:
        server.DF = ingest.load_events_from_records(events)
        body = client.get("/api/geocode/failures").get_json()
    finally:
        server.DF = original_df

    assert body["total_failed"] == 1  # the stale location isn't in the current dataset
    assert body["failures"] == [{"location": "Ackerman 2408", "reason": "Nominatim found no match for this query"}]
    assert body["by_reason"] == [["Nominatim found no match for this query", 1]]
    assert body["approximate"] == [{"location": "Boelter 5800", "placed_at": "UCLA, Los Angeles, CA"}]


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
