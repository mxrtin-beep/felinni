"""Generates data/sample_events.json: synthetic events in the exact schema
CalendarExporter emits, used for tests and for trying the CLI without a
real calendar export."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(7)

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_events.json"

START = datetime(2021, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)

FRIENDS = ["Alice", "Bob", "Carla", "Diego", "Emi"]
GYMS = ["Equinox - Union Square", "Equinox - SoMa", "Crunch - Mission"]
RESTAURANTS = ["Tartine", "State Bird", "Zuni Cafe", "Nopa"]
BARS = ["The Alembic", "Trick Dog"]
TRIP_DESTINATIONS = ["Tokyo, Japan", "Lisbon, Portugal", "New York, NY"]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_note_tags(notes: str | None) -> dict:
    """Mirrors CalendarExporter's NoteTagParser so synthetic fixtures match
    what the real exporter would produce."""
    if not notes:
        return {}
    tags: dict[str, list[str]] = {}
    for line in notes.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip().lower()
        values = [v.strip() for v in rest.split(",") if v.strip()]
        if key and values:
            tags.setdefault(key, []).extend(values)
    return tags


def make_event(idx, title, start, hours, calendar, location=None, notes=None, attendees=None):
    end = start + timedelta(hours=hours)
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": notes,
        "location": location,
        "startDate": iso(start),
        "endDate": iso(end),
        "isAllDay": False,
        "calendarTitle": calendar,
        "attendees": attendees or [],
        "isRecurring": calendar == "Gym",
        "url": None,
        "noteTags": parse_note_tags(notes),
    }


def main():
    events = []
    idx = 0
    day = START

    # A gym habit that's consistent through 2023, then fades in 2024-2025
    # (simulating "attendance dips during busy work weeks"), and a work
    # crunch block in late 2024 that should show up as anomalously busy.
    while day < END:
        weekday = day.weekday()
        year = day.year

        gym_probability = {2021: 0.5, 2022: 0.6, 2023: 0.65, 2024: 0.25, 2025: 0.35, 2026: 0.4}[year]
        if weekday in (0, 2, 4) and random.random() < gym_probability:
            events.append(make_event(
                idx, "Gym", day.replace(hour=7), 1.0, "Gym", location=random.choice(GYMS),
            ))
            idx += 1

        if weekday == 4 and random.random() < 0.3:
            friend = random.choice(FRIENDS)
            events.append(make_event(
                idx, f"Dinner with {friend}", day.replace(hour=19), 2.0, "Social",
                location=random.choice(RESTAURANTS), attendees=[friend],
            ))
            idx += 1

        if weekday == 5 and random.random() < 0.15:
            friend = random.choice(FRIENDS[:2])  # Alice/Bob seen more -> growing trend
            events.append(make_event(
                idx, f"Drinks with {friend}", day.replace(hour=20), 2.5, "Social",
                location=random.choice(BARS), attendees=[friend],
            ))
            idx += 1

        # Simulated late-2024 work crunch: dense recurring "Work" blocks.
        if datetime(2024, 10, 1, tzinfo=timezone.utc) <= day <= datetime(2024, 11, 15, tzinfo=timezone.utc):
            if weekday < 5:
                events.append(make_event(idx, "Work crunch", day.replace(hour=9), 10.0, "Work"))
                idx += 1

        day += timedelta(days=1)

    # A handful of trips.
    for i, dest in enumerate(TRIP_DESTINATIONS):
        trip_start = START + timedelta(days=200 + i * 400)
        for d in range(3):
            events.append(make_event(
                idx, f"Trip to {dest.split(',')[0]}", trip_start + timedelta(days=d, hours=10), 6.0,
                "Travel", location=dest,
            ))
            idx += 1

    # A first date, for the dating-link cross-reference example.
    date_day = START + timedelta(days=100)
    events.append(make_event(
        idx, "First date", date_day.replace(hour=19), 2.0, "Dating",
        location="Nopa", attendees=["Jordan"], notes="Category: Date",
    ))
    idx += 1

    # A messy old event: no location, no attendees, just a title.
    events.append(make_event(idx - 1, "Doctor appt", START + timedelta(days=5, hours=14), 1.0, "Personal"))

    events.sort(key=lambda e: e["startDate"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(events, indent=2))
    print(f"Wrote {len(events)} synthetic events to {OUT_PATH}")


if __name__ == "__main__":
    main()
