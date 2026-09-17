# felinni

Retroactively mine your own Apple Calendar for patterns: where you've been,
who you've spent time with, habit consistency, travel history, and
anomalous weeks.

![Overview tab](docs/images/overview.png)

## Quickstart

**1. Export your calendar (on a Mac — EventKit is macOS/iOS-only):**

```bash
cd CalendarExporter
swift run CalendarExporter --start 2015-01-01 --end 2026-09-16 --output ../events.json
```

First run prompts for Calendar access. `--calendars "Gym,Social,Dating"` limits
to specific calendars. Nothing here calls out to a server — `events.json`
never leaves your machine.

**2. Browse the dashboard (works anywhere):**

```bash
cd analysis
pip install -r requirements.txt
python webapp/server.py --events ../events.json
```

Open **http://127.0.0.1:5000**. No real export yet? Try it against the
committed synthetic fixture: `python webapp/server.py --events ../data/sample_events.json`.

## Tagging conventions

The toolkit works with whatever you have, but a bit of structure goes a long way:

| What | How |
|---|---|
| **Category** | A dedicated Calendar per category, or a `Category: Gym` note line (wins if both present) |
| **People** | Attendees, a `People: Alice, Bob` note line, or a trailing "Dinner with John Doe, Jane Doe" in the title |
| **Location** | The Location field, or a `Location: <place>` note line. Meeting links/phone numbers are auto-filtered out |
| **Travel** | Category "Travel"/"Trip"/"Flight"/"Vacation", or "Flight to...", "Trip to..." in the title |
| **Dating** | Category "Date" + the person tagged, to enable `felinni.dating_link` |

Messier/older events (no location, no people) are handled too — they just
don't contribute to analyses that need that field.

## What's in the dashboard

Tabs: **Overview · Places · Map · People · Habits · Travel · Time & Spend ·
Seasonality · Anomalies · Future**. Every table is sortable (click a header).
The Overview tab's date range and category checkboxes apply globally; each
tab's own filters stack on top.

![People tab](docs/images/people.png)

- **People** — frequency, growing/fading relationships, an "events over
  time" chart with a dropdown for who to plot and a draggable range slider
  to zoom into a sub-range. All-day events are excluded everywhere hours
  are counted, so a full-day placeholder doesn't inflate anyone's total.
- **Habits & repeating events** — streaks/gaps for any category you pick,
  plus auto-detected recurring series (Book Club, Standup, ...) flagged
  active/slowing down/stopped against their own historical cadence.

  ![Habits tab](docs/images/habits.png)
- **Map & Travel** — click **Geocode locations** to plot everything
  (OpenStreetMap Nominatim, cached to disk, ~1/sec). Nearby cities group
  into one metro area for trip-counting, with a neighborhood drill-down for
  your home area. Wrong pin? Fix it inline from the **Fix a location** card
  — no re-geocoding needed.
- **Import calendars** (Overview tab) — pull in Google/Outlook/a second
  Apple calendar via their "secret ICS link" (no OAuth), or upload a file.
  ICS-link sources refresh automatically every 30 min while the server
  runs. Duplicate events across sources are matched and only counted once.
- **Phases of Life** (Overview tab) — a Gantt-style timeline of which
  category was consistently active when.
- **Future** — a skeleton for suggesting upcoming events (Eventbrite/Luma/
  Meetup) ranked by fit with your habits. Not implemented — no API access
  is configured for any of the three — just laid out for later.

### Or skip the browser

```bash
python cli.py --events ../events.json places
python cli.py --events ../events.json people
python cli.py --events ../events.json habit --category Gym
python cli.py --events ../events.json travel
python cli.py --events ../events.json anomalies
```

Or from a notebook: `felinni.ingest.load_events` returns a plain pandas
DataFrame; every other module (`location`, `social`, `habits`, `regions`, ...)
is a pure function over it, and `webapp/server.py` is a thin JSON wrapper
over the same functions.

## Testing without a real calendar

```bash
python3 tests/make_sample_data.py   # regenerates data/sample_events.json
python3 -m pytest tests/
```

## Project layout

```
CalendarExporter/          Swift package: EventKit -> events.json
analysis/
  cli.py                    Command-line entry point
  felinni/                  ingest, geocode, calendar_sources, and one module per analysis
  webapp/                   Flask API (server.py) + static frontend (index.html/app.js/charts.js)
tests/                      pytest suite + make_sample_data.py (synthetic fixture generator)
data/                       sample_events.json (committed) + gitignored caches (geocode, calendar sources)
```
