"""Tests for felinni.calendar_sources: ICS parsing into the exporter's
event schema, and the source manifest (add/list/sync/hide/delete)."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import calendar_sources, ingest

SAMPLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
X-WR-CALNAME:My Google Calendar
BEGIN:VEVENT
UID:abc123
SUMMARY:Coffee with Alice
DESCRIPTION:Category: Social\\nPeople: Alice
LOCATION:Blue Bottle
DTSTART:20240102T170000Z
DTEND:20240102T180000Z
ATTENDEE;CN=Alice Smith:mailto:alice@example.com
RRULE:FREQ=WEEKLY
URL:https://example.com/event
END:VEVENT
BEGIN:VEVENT
UID:allday1
SUMMARY:Trip to Tokyo
DTSTART;VALUE=DATE:20240301
DTEND;VALUE=DATE:20240303
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_bytes_extracts_calendar_name_and_events():
    events = calendar_sources.parse_ics_bytes(SAMPLE_ICS, "src1", "fallback name")
    assert len(events) == 2
    timed = next(e for e in events if e["title"] == "Coffee with Alice")
    assert timed["calendarTitle"] == "My Google Calendar"
    assert timed["location"] == "Blue Bottle"
    assert timed["isAllDay"] is False
    assert timed["startDate"] == "2024-01-02T17:00:00Z"
    assert timed["endDate"] == "2024-01-02T18:00:00Z"
    assert timed["isRecurring"] is True
    assert timed["url"] == "https://example.com/event"
    assert timed["noteTags"] == {"category": ["Social"], "people": ["Alice"]}
    assert "Alice Smith" in timed["attendees"]
    assert timed["id"] == "src1:abc123"


def test_parse_ics_bytes_handles_all_day_events():
    events = calendar_sources.parse_ics_bytes(SAMPLE_ICS, "src1", "fallback name")
    trip = next(e for e in events if e["title"] == "Trip to Tokyo")
    assert trip["isAllDay"] is True
    assert trip["startDate"] == "2024-03-01T00:00:00Z"


def test_parse_ics_bytes_falls_back_to_given_name_without_calname():
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:x1
SUMMARY:Standup
DTSTART:20240102T170000Z
DTEND:20240102T173000Z
END:VEVENT
END:VCALENDAR
"""
    events = calendar_sources.parse_ics_bytes(ics, "src2", "Work (Outlook)")
    assert events[0]["calendarTitle"] == "Work (Outlook)"


def test_parsed_ics_events_load_cleanly_through_ingest(tmp_path):
    events = calendar_sources.parse_ics_bytes(SAMPLE_ICS, "src1", "fallback")
    df = ingest.load_events_from_records(events)
    assert len(df) == 2
    assert "Coffee with Alice" in df["title"].values
    assert df[df["title"] == "Coffee with Alice"].iloc[0]["category"] == "Social"


def test_add_source_ics_url_syncs_immediately(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    fake_response = MagicMock()
    fake_response.content = SAMPLE_ICS
    fake_response.raise_for_status = MagicMock()

    with patch("requests.get", return_value=fake_response):
        entry = calendar_sources.add_source(
            "My Google Calendar", "google", "ics_url", url="https://example.com/cal.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )

    assert entry["event_count"] == 2
    assert entry["last_synced"] is not None
    assert entry["last_sync_error"] is None
    events = calendar_sources.load_source_events(entry["id"], sources_dir)
    assert len(events) == 2


def test_add_source_records_error_without_losing_the_source(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    with patch("requests.get", side_effect=OSError("network unreachable")):
        entry = calendar_sources.add_source(
            "Broken link", "outlook", "ics_url", url="https://example.com/bad.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )
    assert entry["last_sync_error"] == "network unreachable"
    assert entry["event_count"] == 0
    assert calendar_sources.list_sources(manifest_path)  # still registered


def test_add_source_events_json_file_upload(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    raw_events = [{
        "id": "evt-1", "title": "Gym", "notes": None, "location": None,
        "startDate": "2024-01-01T09:00:00Z", "endDate": "2024-01-01T10:00:00Z",
        "isAllDay": False, "calendarTitle": "Gym", "calendarColorHex": None,
        "attendees": [], "isRecurring": False, "url": None, "noteTags": {},
    }]
    entry = calendar_sources.add_source(
        "My iPhone export", "apple", "events_json",
        file_bytes=json.dumps(raw_events).encode(),
        manifest_path=manifest_path, sources_dir=sources_dir,
    )
    assert entry["event_count"] == 1
    events = calendar_sources.load_source_events(entry["id"], sources_dir)
    assert events[0]["id"] == f"{entry['id']}:evt-1"  # namespaced to avoid id collisions


def test_set_visibility_hides_source_from_merge(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    with patch("requests.get", return_value=MagicMock(content=SAMPLE_ICS, raise_for_status=MagicMock())):
        entry = calendar_sources.add_source(
            "Cal", "google", "ics_url", url="https://example.com/cal.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )

    assert len(calendar_sources.merged_source_events(manifest_path, sources_dir)) == 2
    calendar_sources.set_visibility(entry["id"], False, manifest_path)
    assert calendar_sources.merged_source_events(manifest_path, sources_dir) == []
    calendar_sources.set_visibility(entry["id"], True, manifest_path)
    assert len(calendar_sources.merged_source_events(manifest_path, sources_dir)) == 2


def test_remove_source_deletes_manifest_entry_and_cache(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    with patch("requests.get", return_value=MagicMock(content=SAMPLE_ICS, raise_for_status=MagicMock())):
        entry = calendar_sources.add_source(
            "Cal", "google", "ics_url", url="https://example.com/cal.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )
    assert calendar_sources.remove_source(entry["id"], manifest_path, sources_dir) is True
    assert calendar_sources.list_sources(manifest_path) == []
    assert calendar_sources.load_source_events(entry["id"], sources_dir) == []
    assert calendar_sources.remove_source(entry["id"], manifest_path, sources_dir) is False


def test_sync_source_refetches_ics_url(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    with patch("requests.get", return_value=MagicMock(content=SAMPLE_ICS, raise_for_status=MagicMock())):
        entry = calendar_sources.add_source(
            "Cal", "google", "ics_url", url="https://example.com/cal.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )

    single_event_ics = SAMPLE_ICS.split(b"BEGIN:VEVENT")[0] + b"BEGIN:VEVENT" + SAMPLE_ICS.split(b"BEGIN:VEVENT")[1] + b"END:VCALENDAR\n"
    with patch("requests.get", return_value=MagicMock(content=single_event_ics, raise_for_status=MagicMock())):
        updated = calendar_sources.sync_source(entry["id"], manifest_path, sources_dir)
    assert updated["event_count"] == 1


def test_sync_source_rejects_file_based_sources(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    raw_events = []
    entry = calendar_sources.add_source(
        "Upload", "apple", "events_json", file_bytes=json.dumps(raw_events).encode(),
        manifest_path=manifest_path, sources_dir=sources_dir,
    )
    with pytest.raises(ValueError):
        calendar_sources.sync_source(entry["id"], manifest_path, sources_dir)


def test_sync_all_url_sources_skips_file_based_ones(tmp_path):
    manifest_path = tmp_path / "sources.json"
    sources_dir = tmp_path / "sources"
    calendar_sources.add_source(
        "Upload", "apple", "events_json", file_bytes=b"[]",
        manifest_path=manifest_path, sources_dir=sources_dir,
    )
    with patch("requests.get", return_value=MagicMock(content=SAMPLE_ICS, raise_for_status=MagicMock())):
        calendar_sources.add_source(
            "Cal", "google", "ics_url", url="https://example.com/cal.ics",
            manifest_path=manifest_path, sources_dir=sources_dir,
        )
        touched = calendar_sources.sync_all_url_sources(manifest_path, sources_dir)
    assert len(touched) == 1
    assert touched[0]["kind"] == "ics_url"


def _event_stub(idx, title, start, end):
    return {
        "id": f"evt-{idx}", "title": title, "notes": None, "location": None,
        "startDate": start, "endDate": end, "isAllDay": False,
        "calendarTitle": "Cal", "calendarColorHex": None, "attendees": [],
        "isRecurring": False, "url": None, "noteTags": {},
    }


def test_dedupe_events_drops_matching_title_start_end():
    primary = [_event_stub(1, "Gym", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z")]
    imported = [
        _event_stub(2, "Gym", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z"),  # same event, re-imported
        _event_stub(3, "Coffee", "2024-01-02T09:00:00Z", "2024-01-02T10:00:00Z"),  # genuinely new
    ]
    deduped = calendar_sources.dedupe_events(primary + imported)
    assert len(deduped) == 2
    assert deduped[0]["id"] == "evt-1"  # the primary copy wins, not the imported duplicate
    assert {e["title"] for e in deduped} == {"Gym", "Coffee"}


def test_dedupe_events_is_case_insensitive_on_title():
    events = [
        _event_stub(1, "Gym", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z"),
        _event_stub(2, "GYM", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z"),
    ]
    assert len(calendar_sources.dedupe_events(events)) == 1


def test_dedupe_events_keeps_events_with_different_times():
    events = [
        _event_stub(1, "Gym", "2024-01-01T09:00:00Z", "2024-01-01T10:00:00Z"),
        _event_stub(2, "Gym", "2024-01-02T09:00:00Z", "2024-01-02T10:00:00Z"),
    ]
    assert len(calendar_sources.dedupe_events(events)) == 2
