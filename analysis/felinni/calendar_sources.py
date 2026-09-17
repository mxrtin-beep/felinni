"""Imported calendar sources: a manifest of calendars pulled in from Apple,
Google, Outlook (or anywhere else that can hand you an .ics), on top of the
primary `events.json` export.

Two ways to bring one in, both handled the same way once parsed:
  - `ics_url`: a "secret address in iCal format" link - Google Calendar
    Settings > [calendar] > "Integrate calendar", Outlook Calendar
    Settings > "Shared calendars" > "Publish a calendar", or an iCloud
    public calendar link. Re-fetched periodically (see webapp/server.py's
    background poller) and by a manual "Refresh now".
  - `ics_file` / `events_json`: a one-off uploaded file (an exported .ics,
    or CalendarExporter's own events.json for Apple) - not re-fetched
    automatically since there's no URL to poll; re-upload (or delete and
    re-add) to bring in new events.

Each source's parsed events are cached to disk in the exporter's schema
(one JSON file per source under `data/sources/`), so `webapp/server.py`
can merge the primary events.json with every *visible* source into one
dataset via `felinni.ingest.load_events_from_records` - the same
normalization either way, whether an event came from EventKit or an ICS
link.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_MANIFEST_PATH = _DATA_DIR / "calendar_sources.json"
DEFAULT_SOURCES_DIR = _DATA_DIR / "sources"

PROVIDERS = ("apple", "google", "outlook", "other")
KINDS = ("ics_url", "ics_file", "events_json")

# Mirrors CalendarExporter's NoteTagParser (ExportedEvent.swift): freeform
# "Key: v1, v2" lines pulled out of an event's notes/description, e.g.
# "Category: Gym" - so an ICS-imported event tagged the same way parses
# identically to one from the Swift exporter.
_NOTE_TAG_LINE = re.compile(r"^([^:]{1,31}):\s*(.+)$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_note_tags(notes: str | None) -> dict[str, list[str]]:
    if not notes:
        return {}
    result: dict[str, list[str]] = {}
    for raw_line in notes.splitlines():
        match = _NOTE_TAG_LINE.match(raw_line.strip())
        if not match:
            continue
        key = match.group(1).strip().lower()
        if not key:
            continue
        values = [v.strip() for v in match.group(2).split(",") if v.strip()]
        if values:
            result.setdefault(key, []).extend(values)
    return result


def _dt_to_iso_z(value) -> str:
    """A date or datetime (as icalendar hands back from DTSTART/DTEND) to
    the exporter's "...Z" UTC string format. A plain date (all-day event)
    becomes midnight UTC that day."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.strftime("%Y-%m-%dT00:00:00Z")


def _attendee_names(component) -> list[str]:
    raw = component.get("attendee")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    names = []
    for attendee in raw:
        cn = attendee.params.get("CN") if hasattr(attendee, "params") else None
        names.append(str(cn) if cn else str(attendee).replace("mailto:", ""))
    return names


def _event_from_vevent(component, calendar_title: str, source_id: str, idx: int) -> dict | None:
    dtstart = component.get("dtstart")
    if dtstart is None:
        return None  # malformed VEVENT - nothing to anchor a date to
    start_val = dtstart.dt
    dtend = component.get("dtend")
    end_val = dtend.dt if dtend is not None else start_val
    is_all_day = not isinstance(start_val, datetime)

    uid = str(component.get("uid") or f"{source_id}-{idx}")
    notes = str(component.get("description")) if component.get("description") else None
    location = component.get("location")
    url = component.get("url")
    return {
        "id": f"{source_id}:{uid}",
        "title": str(component.get("summary") or ""),
        "notes": notes,
        "location": str(location) if location else None,
        "startDate": _dt_to_iso_z(start_val),
        "endDate": _dt_to_iso_z(end_val),
        "isAllDay": is_all_day,
        "calendarTitle": calendar_title,
        "calendarColorHex": None,
        "attendees": _attendee_names(component),
        "isRecurring": component.get("rrule") is not None,
        "url": str(url) if url else None,
        "noteTags": _parse_note_tags(notes),
    }


def parse_ics_bytes(ics_bytes: bytes, source_id: str, default_calendar_title: str) -> list[dict]:
    """Parses raw .ics bytes into the exporter's event-dict schema (the
    same shape `felinni.ingest.load_events_from_records` expects). Each
    event's calendar is the ICS's own name (X-WR-CALNAME) if it has one,
    else the source's given name."""
    try:
        import icalendar
    except ImportError as e:
        raise ImportError("parsing an .ics calendar requires icalendar: pip install icalendar") from e

    cal = icalendar.Calendar.from_ical(ics_bytes)
    calendar_title = str(cal.get("X-WR-CALNAME") or default_calendar_title)
    events = []
    for idx, component in enumerate(cal.walk("VEVENT")):
        event = _event_from_vevent(component, calendar_title, source_id, idx)
        if event:
            events.append(event)
    return events


def _namespace_ids(raw_events: list[dict], source_id: str) -> list[dict]:
    """Prefixes each event's id with the source id, so an events.json
    upload (e.g. a second Apple export) can't collide with the primary
    dataset's or another source's ids."""
    out = []
    for event in raw_events:
        event = dict(event)
        event["id"] = f"{source_id}:{event.get('id')}"
        out.append(event)
    return out


def _events_cache_path(source_id: str, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> Path:
    return Path(sources_dir) / f"{source_id}.json"


def _write_events_cache(source_id: str, events: list[dict], sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> None:
    path = _events_cache_path(source_id, sources_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events))


def load_source_events(source_id: str, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> list[dict]:
    path = _events_cache_path(source_id, sources_dir)
    if path.exists():
        return json.loads(path.read_text())
    return []


def _load_manifest(path: str | Path = DEFAULT_MANIFEST_PATH) -> list[dict]:
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text())
    return []


def _save_manifest(sources: list[dict], path: str | Path = DEFAULT_MANIFEST_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, indent=2))


def list_sources(manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> list[dict]:
    return _load_manifest(manifest_path)


def _sync_one(entry: dict, file_bytes: bytes | None = None, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> None:
    """Fetches/parses one source and writes its cache, mutating `entry`
    with the result. Raises on failure - callers decide whether to record
    the error on the entry or let it propagate."""
    kind = entry["kind"]
    if kind == "ics_url":
        try:
            import requests
        except ImportError as e:
            raise ImportError("fetching an ICS link requires requests: pip install requests") from e
        resp = requests.get(entry["url"], timeout=20)
        resp.raise_for_status()
        events = parse_ics_bytes(resp.content, entry["id"], entry["name"])
    elif kind == "ics_file":
        if not file_bytes:
            raise ValueError("no file provided to sync this source")
        events = parse_ics_bytes(file_bytes, entry["id"], entry["name"])
    elif kind == "events_json":
        if not file_bytes:
            raise ValueError("no file provided to sync this source")
        events = _namespace_ids(json.loads(file_bytes.decode("utf-8")), entry["id"])
    else:
        raise ValueError(f"unknown source kind: {kind!r}")

    _write_events_cache(entry["id"], events, sources_dir)
    entry["event_count"] = len(events)
    entry["last_synced"] = _now_iso()
    entry["last_sync_error"] = None


def add_source(
    name: str,
    provider: str,
    kind: str,
    url: str | None = None,
    file_bytes: bytes | None = None,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    sources_dir: str | Path = DEFAULT_SOURCES_DIR,
) -> dict:
    """Registers a new calendar source and does its first sync
    immediately. A sync failure (bad URL, malformed file) doesn't prevent
    the source from being added - it's recorded on `last_sync_error` so
    the dashboard can show it and the user can retry, rather than losing
    the form input they just filled in."""
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}, got {provider!r}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    if kind == "ics_url" and not url:
        raise ValueError("url is required for kind='ics_url'")
    if kind in ("ics_file", "events_json") and not file_bytes:
        raise ValueError(f"a file upload is required for kind={kind!r}")

    entry = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "provider": provider,
        "kind": kind,
        "url": url,
        "visible": True,
        "added_at": _now_iso(),
        "last_synced": None,
        "last_sync_error": None,
        "event_count": 0,
    }
    sources = _load_manifest(manifest_path)
    sources.append(entry)

    try:
        _sync_one(entry, file_bytes=file_bytes, sources_dir=sources_dir)
    except Exception as e:
        entry["last_sync_error"] = str(e)

    _save_manifest(sources, manifest_path)
    return entry


def sync_source(source_id: str, manifest_path: str | Path = DEFAULT_MANIFEST_PATH, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> dict:
    """Manual "Refresh now" for one ICS-link source. File-based sources
    have nothing to re-fetch from - re-upload (or delete and re-add) to
    update those."""
    sources = _load_manifest(manifest_path)
    entry = next((s for s in sources if s["id"] == source_id), None)
    if entry is None:
        raise KeyError(f"no such source: {source_id!r}")
    if entry["kind"] != "ics_url":
        raise ValueError("this source was imported from a file - re-upload it to update, there's no link to refresh from")
    try:
        _sync_one(entry, sources_dir=sources_dir)
    except Exception as e:
        entry["last_sync_error"] = str(e)
    _save_manifest(sources, manifest_path)
    return entry


def sync_all_url_sources(manifest_path: str | Path = DEFAULT_MANIFEST_PATH, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> list[dict]:
    """Re-fetches every ICS-link source - what the background poller in
    webapp/server.py calls on a timer so links stay reasonably fresh
    without a manual click every time."""
    sources = _load_manifest(manifest_path)
    touched = []
    for entry in sources:
        if entry["kind"] != "ics_url":
            continue
        try:
            _sync_one(entry, sources_dir=sources_dir)
        except Exception as e:
            entry["last_sync_error"] = str(e)
        touched.append(entry)
    if touched:
        _save_manifest(sources, manifest_path)
    return touched


def set_visibility(source_id: str, visible: bool, manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> dict | None:
    sources = _load_manifest(manifest_path)
    entry = next((s for s in sources if s["id"] == source_id), None)
    if entry is None:
        return None
    entry["visible"] = bool(visible)
    _save_manifest(sources, manifest_path)
    return entry


def remove_source(source_id: str, manifest_path: str | Path = DEFAULT_MANIFEST_PATH, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> bool:
    sources = _load_manifest(manifest_path)
    remaining = [s for s in sources if s["id"] != source_id]
    removed = len(remaining) != len(sources)
    if removed:
        _save_manifest(remaining, manifest_path)
        cache_path = _events_cache_path(source_id, sources_dir)
        if cache_path.exists():
            cache_path.unlink()
    return removed


def merged_source_events(manifest_path: str | Path = DEFAULT_MANIFEST_PATH, sources_dir: str | Path = DEFAULT_SOURCES_DIR) -> list[dict]:
    """Every event from every *visible* source, concatenated - what
    webapp/server.py merges with the primary events.json into one dataset.
    A hidden source's events are simply left out (its cache and manifest
    entry are untouched, so re-showing it doesn't need a re-sync)."""
    combined = []
    for entry in _load_manifest(manifest_path):
        if not entry.get("visible", True):
            continue
        combined.extend(load_source_events(entry["id"], sources_dir))
    return combined
