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
# Mix of full Maps-picked addresses and bare venue names, to exercise both
# the address-line splitter and its plain-fallback path.
GYMS = [
    "Equinox Union Square, 301 Grant Ave, San Francisco, CA 94108, United States",
    "Equinox SoMa, 747 Market St, San Francisco, CA 94103, United States",
    "Crunch - Mission",
]
RESTAURANTS = [
    "Tartine Bakery, 600 Guerrero St, San Francisco, CA 94110, United States",
    "State Bird Provisions, 1529 Fillmore St, San Francisco, CA 94115, United States",
    "Zuni Cafe",
    "Nopa",
]
BARS = ["The Alembic", "Trick Dog"]
TRIP_DESTINATIONS = ["Tokyo, Japan", "Lisbon, Portugal", "New York, NY"]

# Distinct from the dashboard's own fixed palette, so a real Calendar.app
# color visibly overrides the fallback rather than coincidentally matching it.
CALENDAR_COLORS = {
    "Gym": "#8E24AA",
    "Work": "#00897B",
    "Social": "#FDD835",
}


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


def make_event(idx, title, start, hours, calendar, location=None, notes=None, attendees=None, is_all_day=False, is_recurring=None):
    end = start + timedelta(hours=hours)
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": notes,
        "location": location,
        "startDate": iso(start),
        "endDate": iso(end),
        "isAllDay": is_all_day,
        "calendarTitle": calendar,
        "calendarColorHex": CALENDAR_COLORS.get(calendar),
        "attendees": attendees or [],
        "isRecurring": (calendar == "Gym") if is_recurring is None else is_recurring,
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

        # Group hangouts, named only in the title (no formal Calendar
        # attendees) - exercises multi-person title parsing, including a
        # curly apostrophe and a non-name trailing token.
        if weekday == 6 and random.random() < 0.1:
            events.append(make_event(
                idx, "Game night with Alice, Bob, Carla, and Diego", day.replace(hour=19), 3.0, "Social",
                location=random.choice(BARS),
            ))
            idx += 1
        if weekday == 6 and random.random() < 0.05:
            events.append(make_event(
                idx, "Potluck with Emi, Carla, and Sean O’Brien", day.replace(hour=18), 2.0, "Social",
                location=random.choice(RESTAURANTS),
            ))
            idx += 1

        # Simulated late-2024 work crunch: dense recurring "Work" blocks.
        if datetime(2024, 10, 1, tzinfo=timezone.utc) <= day <= datetime(2024, 11, 15, tzinfo=timezone.utc):
            if weekday < 5:
                events.append(make_event(idx, "Work crunch", day.replace(hour=9), 10.0, "Work"))
                idx += 1

        # Weekly Zoom standup: location is a meeting link, not a place - the
        # dashboard should filter this out rather than showing "zoom.us" as
        # a top "place". Also exercises title-based people parsing (no
        # attendees tagged in Calendar, just named at the end of the title).
        if weekday == 0:
            events.append(make_event(
                idx, "Standup with Priya Patel and Sam Lee", day.replace(hour=9, minute=30), 0.5, "Work",
                location="https://us02web.zoom.us/j/5551234567?pwd=abc123",
            ))
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
    events.append(make_event(idx, "Doctor appt", START + timedelta(days=5, hours=14), 1.0, "Personal"))
    idx += 1

    # A phone-number location - another junk-location case.
    events.append(make_event(
        idx, "Call with recruiter", START + timedelta(days=40, hours=11), 0.5, "Work",
        location="+1 (415) 555-0182",
    ))
    idx += 1

    # An all-day event: should count for nothing in Time & Spend (no real duration).
    events.append(make_event(
        idx, "Company offsite", START + timedelta(days=250), 24.0, "Work", is_all_day=True,
    ))
    idx += 1

    # A named-venue full address (name prefix before the street number) -
    # exercises the 4-line address split: name / street / city+state / zip+country.
    for i in range(5):
        events.append(make_event(
            idx, "Club meeting", START + timedelta(days=60 + i * 14, hours=18), 1.5, "UCLA Clubs",
            location="eaves Woodland Hills, 22122 Ventura Blvd, Woodland Hills, CA 91367, United States",
        ))
        idx += 1

    # A UCLA building name with no street address at all - relies on the
    # geocode category-anchor (felinni.geocode.DEFAULT_LOCATION_ANCHORS) to
    # land on campus instead of drifting to a same-named place worldwide.
    for i in range(4):
        events.append(make_event(
            idx, "Study session", START + timedelta(days=70 + i * 21, hours=15), 2.0, "UCLA Other",
            location="North Campus Student Center",
        ))
        idx += 1

    # Three named recurring series (a real Calendar repeat rule, not just a
    # shared category), one in each status the recurring-events tracker
    # reports: still going, drifting, and long stopped.
    for i in range(60):  # weekly for ~14 months, still going through the fixture's end
        events.append(make_event(
            idx, "Book Club", END - timedelta(weeks=60 - i, hours=-19), 1.5, "Social",
            location="Zuni Cafe", is_recurring=True,
        ))
        idx += 1
    # Biweekly, gaps widening over time, with the last occurrence ~10 weeks
    # before the fixture's end - "slowing down" (drifted well past its old
    # cadence, but not so long ago as to read as fully stopped).
    poker_gap_weeks = [2 + i // 6 for i in range(30)]
    poker_offsets_days = [sum(poker_gap_weeks[:i]) * 7 for i in range(30)]
    poker_last_target = END - timedelta(weeks=10)
    poker_shift_days = (poker_last_target - (START + timedelta(days=poker_offsets_days[-1]))).days
    for i, offset in enumerate(poker_offsets_days):
        events.append(make_event(
            idx, "Poker Night", START + timedelta(days=offset + poker_shift_days, hours=20),
            2.5, "Social", location="The Alembic", is_recurring=True,
        ))
        idx += 1
    for i in range(40):  # weekly through 2022, then nothing since - "stopped"
        events.append(make_event(
            idx, "Standup", datetime(2021, 6, 1, tzinfo=timezone.utc) + timedelta(weeks=i, hours=9),
            0.5, "Work", is_recurring=True,
        ))
        idx += 1

    events.sort(key=lambda e: e["startDate"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(events, indent=2))
    print(f"Wrote {len(events)} synthetic events to {OUT_PATH}")


if __name__ == "__main__":
    main()
