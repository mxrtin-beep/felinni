# felinni

Retroactively mine your own Apple Calendar for patterns: where you've been,
who you've spent time with, habit consistency, travel history, and
anomalous weeks. Two pieces:

1. **CalendarExporter** — a small macOS/Swift CLI using EventKit that reads
   your calendar and exports it to a normalized `events.json`.
2. **analysis/** — a Python toolkit that reads `events.json` and answers the
   questions in the "Analyses" section below.

EventKit is a macOS/iOS-only framework, so step 1 has to run on your Mac —
it won't build or run in this container. Step 2 is plain Python and works
anywhere (this repo's tests run it against a synthetic fixture).

## 1. Export your calendar (on your Mac)

```bash
cd CalendarExporter
swift run CalendarExporter --start 2015-01-01 --end 2026-09-16 --output ../events.json
```

The first run prompts for Calendar access (macOS Settings > Privacy &
Security > Calendars if you need to re-grant it later). Use `--calendars
"Gym,Social,Dating"` to limit to specific calendars, and `--help` for all
options.

Nothing here calls out to a server: `events.json` never leaves your
machine unless you move it yourself.

### How events should be tagged for the richest analysis

The toolkit works with whatever you have, but gets much more out of events
that carry structure:

- **Category**: use a dedicated Calendar per category (e.g. "Gym",
  "Social", "Dating", "Travel", "Work"), or add a `Category: Gym` line to
  an event's Notes — the note tag wins if both are present.
- **People**: add attendees on the event, or a note line like
  `People: Alice, Bob`.
- **Location**: the event's Location field, or a `Location: <place>` note
  line (useful when the title has the info but Location is blank).
- **Travel**: tag trip events with category "Travel" (or "Trip"/"Flight").
- **Dating**: tag first dates with category "Date" and the person as an
  attendee/`People:` tag, to enable the cross-reference in
  `felinni.dating_link`.

Messier/older events (no location, no people) are handled too — they just
don't contribute to analyses that need that field.

## 2. Browse the dashboard

```bash
cd analysis
pip install -r requirements.txt   # geopy/folium are optional, only for mapping
python webapp/server.py --events ../events.json
```

Open **http://127.0.0.1:5000**. It's a single page with tabs — Overview,
Places, People, Habits, Travel, Time & Spend, Seasonality, Anomalies —
each backed by one of the analyses below, with charts and tables you can
click through instead of running commands. It's a plain Flask dev server
reading your local `events.json`, nothing leaves your machine.

Try it against the synthetic fixture first if you don't have a real
export yet: `python webapp/server.py --events ../data/sample_events.json`.

### Map tab

The Map tab plots your geocoded locations, filterable by category, person,
and year range, with circle size = visit count and color = category. It
needs coordinates for your locations first, which is a separate opt-in
step since it calls out to OpenStreetMap's Nominatim geocoder over the
network (nothing else in this repo does):

```bash
python cli.py --events ../events.json geocode
```

This geocodes every unique location string once (~1 request/sec, so it
can take a few minutes for a big calendar) and caches the results to
`data/geocode_cache.json`. Re-running it only geocodes new locations.
Until you run it, the Map tab tells you so instead of showing an empty map.

### Or use the CLI / library directly

```bash
python cli.py --events ../events.json places
python cli.py --events ../events.json stopped-going --min-visits 3 --inactive-months 9
python cli.py --events ../events.json people
python cli.py --events ../events.json trends
python cli.py --events ../events.json habit --category Gym
python cli.py --events ../events.json travel
python cli.py --events ../events.json time-by-category
python cli.py --events ../events.json seasonality --category Gym
python cli.py --events ../events.json anomalies
```

Or from a notebook — `felinni.ingest.load_events` returns a plain pandas
DataFrame; everything else (`felinni.location`, `.social`, `.habits`, ...)
is a pure function over that DataFrame, and `webapp/server.py`'s routes
are a thin JSON wrapper over the same functions.

## Analyses, and where they live

| Ask | Module | Notes |
|---|---|---|
| Map every place, cluster by neighborhood, radius of life over time, places you stopped going to | `felinni.location` | Neighborhood clustering and radius-of-life need geocoded coordinates (`felinni.geocode`, opt-in, uses OpenStreetMap Nominatim, cached to disk) |
| Frequency of seeing people, growing/fading relationships, social time split | `felinni.social` | Needs attendees or `People:`/`With:` note tags |
| Habit streaks/drop-offs, correlate with busy weeks | `felinni.habits` | Pass any category as the "habit" (Gym, Therapy, ...) |
| Travel timeline, places visited | `felinni.travel` | Collapses consecutive same-destination events into one trip |
| Time (and estimated spend) by category | `felinni.spending` | You supply the per-visit cost assumptions in `DEFAULT_COST_PER_VISIT` — nothing is invented |
| Seasonality by month/season | `felinni.seasonality` | |
| Unusually packed/empty weeks | `felinni.anomalies` | Z-score on weekly scheduled hours |
| Link "first date" events to a Hinge/iMessage timestamp table | `felinni.dating_link` | Matches by tagged person + nearest timestamp within a configurable window |

## Testing without a real calendar

`tests/make_sample_data.py` generates `data/sample_events.json`, a
synthetic multi-year calendar (gym habit that fades during a simulated
work crunch, a handful of friends with growing/fading trends, three
trips, one tagged date) in the exact schema the exporter produces. Run:

```bash
python3 tests/make_sample_data.py
python3 -m pytest tests/
```

## Project layout

```
CalendarExporter/          Swift package: EventKit -> events.json
  Sources/CalendarExporter/
    main.swift              CLI entry point, arg parsing, EventKit fetch
    CalendarAccess.swift     EventKit permission handling (old + new APIs)
    ExportedEvent.swift      JSON schema + note-tag parsing
analysis/
  cli.py                    Command-line entry point
  felinni/
    ingest.py                events.json -> pandas DataFrame
    geocode.py                optional Nominatim geocoding, disk-cached
    location.py, social.py, habits.py, travel.py, spending.py,
    seasonality.py, anomalies.py, dating_link.py
  webapp/
    server.py                 Flask API wrapping felinni's analysis functions
    serialize.py               DataFrame -> JSON-safe records
    static/                    index.html, app.js, charts.js (hand-built SVG), styles.css
    static/vendor/leaflet/     vendored Leaflet (map tab), no CDN dependency
tests/
  make_sample_data.py        synthetic fixture generator
  test_analysis.py
  test_webapp.py             smoke tests for the dashboard's API
data/
  sample_events.json         generated fixture (committed for convenience)
  geocode_cache.json         built by `cli.py geocode`, gitignored (your location history stays local)
```
