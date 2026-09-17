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
- **People**: add attendees on the event, a note line like `People: Alice,
  Bob`, or just name them at the end of the title, e.g. "Dinner with John
  Doe, Jane Doe, and McLovin" — parsed automatically if nothing else tagged
  people on that event (and only when what follows "with" looks like an
  actual name list, so "lunch with the whole team" is left alone). If you
  refer to someone inconsistently (sometimes "Alice", sometimes "Alice
  Smith"), a bare first name tagged on a *group* event is resolved to that
  first name's most common full form elsewhere in your calendar, so it
  doesn't get counted as a separate person. A first name on a 1:1 event is
  left alone instead - there's no other name on that event to confirm
  which "Alice" it is.
- **Location**: the event's Location field, or a `Location: <place>` note
  line (useful when the title has the info but Location is blank). A
  location that's actually a meeting link (Zoom/Meet/Teams/Webex) or a
  phone number is filtered out automatically rather than showing up as a
  "place".
- **Travel**: tag trip events with category "Travel"/"Trip"/"Flight"/
  "Vacation"/"Vacay"/"Holiday", or just put "Flight to...", "Trip to...",
  or "Vacation" in the title — matched automatically if nothing else
  tagged the category. The Travel tab tells you exactly what it searched
  for if nothing matches.
- **Dating**: tag first dates with category "Date" and the person as an
  attendee/`People:` tag, to enable the cross-reference in
  `felinni.dating_link`.

Messier/older events (no location, no people) are handled too — they just
don't contribute to analyses that need that field.

## 2. Browse the dashboard

```bash
cd analysis
pip install -r requirements.txt   # geopy/folium: mapping; requests/icalendar: importing other calendars
python webapp/server.py --events ../events.json
```

Open **http://127.0.0.1:5000**. It's a single page with tabs — Overview,
Places, Map, People, Habits, Travel, Time & Spend, Seasonality, Anomalies,
Future — each backed by one of the analyses below, with charts and tables
you can click through instead of running commands. Every table is
sortable - click a column header to sort by it, click again to reverse.
It's a plain Flask dev server reading your local `events.json`, nothing
leaves your machine.

### Future tab (skeleton, not implemented)

Lays out what "suggest upcoming events worth going to, from Eventbrite/
Luma/Meetup, ranked by fit with your own history" would look like -
`felinni.future_events` and `/api/future` always return empty data, and
the tab's Connect buttons are disabled. Nothing here calls out to any of
those three - each needs its own API access (Eventbrite/Luma: an API key,
Meetup: OAuth) that isn't configured. It's there so a real integration has
a shape to build into rather than starting from a blank tab.

The Overview tab also has a **Phases of Life** timeline — one row per
category, with a bar for each long (8+ week) stretch it was consistently
active, on a shared year-gridlined time axis (Gantt-style), colored to
match that category everywhere else in the dashboard. `felinni.breaks`
also computes extended unusually-quiet stretches and a rough "did my home
base change" signal (from which raw location dominates each quarter);
those aren't surfaced in the dashboard but are available from the `/api/breaks`
endpoint or the module directly if you want them.

### Importing other calendars (Google, Outlook, a second Apple calendar, ...)

`events.json` doesn't have to be your only source. The Overview tab's
**Import calendars** card adds calendars from anywhere else, merged in
alongside it everywhere in the dashboard, with a list underneath of
what's imported — each one showing its sync status, a checkbox to
show/hide it (excluded from every analysis without deleting it), and a
delete button.

Two ways to bring one in:

- **Secret ICS link** (Google, Outlook, or an iCloud calendar's public
  sharing link) — paste the URL and it's fetched and parsed immediately.
  No account connection or API keys needed:
  - **Google Calendar**: Settings → [pick a calendar] → "Integrate
    calendar" → **Secret address in iCal format**.
  - **Outlook/Office 365**: Calendar settings → "Shared calendars" →
    **Publish a calendar** → pick the ICS link.
  - **Apple/iCloud**: Calendar app → share a calendar → "Public Calendar"
    → copy the `webcal://` link (use `https://` instead of `webcal://`).

  These refresh **automatically every 30 minutes** while
  `webapp/server.py` is running (`--sync-interval-minutes` to change
  that, or `0` to disable polling), plus a **Refresh now** button for an
  on-demand pull. This isn't a true always-on sync — it only runs while
  the dev server process is up, same as the rest of this dashboard.
- **Upload a file** — a plain `.ics` export, or (for Apple specifically)
  the `events.json` CalendarExporter itself produces, so a second Mac's
  export can be layered in without merging JSON files by hand. A file
  doesn't have a URL to re-fetch from, so updating it means uploading
  again (or deleting and re-adding).

Requires the optional `requests`/`icalendar` dependencies (`pip install
-r requirements.txt` gets both); an ICS-link source records a fetch/parse
failure on itself (bad link, network hiccup) rather than losing the
calendar you just configured, so you can just click **Refresh now** once
it's fixed.

If the same real event shows up from more than one place - your own
events.json and a shared/public link for that same Apple calendar, say -
it's only counted once: events are matched on exact title + start + end
across every source, and when two match, the one from events.json (or
whichever was added first) wins over the later duplicate.

Try it against the synthetic fixture first if you don't have a real
export yet: `python webapp/server.py --events ../data/sample_events.json`.

### Date range and category filters

The Overview tab has a From/To date range and a checkbox per category,
both of which apply to every other tab — narrow the range or uncheck
"Birthdays" (or anything else) and Places, People, Habits, everything
else recomputes accordingly. Each tab's own filters (a habit's category,
the Map's person/year filters, ...) stack on top of these. Reset/All put
things back to your calendar's full span and every category.

### Map tab

The Map tab plots your geocoded locations, filterable by category, person,
and year range, with circle size = visit count and color = category. On
first load it zooms to your densest cluster of places (typically home)
rather than zooming out to fit one far-flung trip too — filtering still
refits to whatever currently matches. It needs coordinates for your
locations first — click **Geocode locations**
on the Map tab, which shows a live progress bar while it calls out to
OpenStreetMap's Nominatim geocoder in the background (~1 request/sec, so
a few minutes for a big calendar; nothing else in this repo makes network
calls). The button disables itself once everything's geocoded, and the
job keeps running even if you switch tabs or the button's tab isn't open.

The same thing is available as a one-shot CLI command if you'd rather not
wait in the browser:

```bash
python cli.py --events ../events.json geocode
```

Either way, results are cached to `data/geocode_cache.json`; re-running
only geocodes newly-seen locations by default. A location that failed to
geocode is always retried on the next run rather than stuck as a
permanent failure - Nominatim misses are often transient. Check **Redo
already-geocoded locations** before clicking the button to force every
location to be re-queried instead (a manual override always wins and is
never re-queried, even with this checked).

Nominatim (the free geocoder behind this) does get things wrong,
especially for a bare building/room name with no street address (e.g.
"North Campus Student Center" landing in a same-named place worldwide) or
even well-known places when its ranking picks a wrong match (a real
report: "Santa Monica Pier" landing in Europe). Three ways to fix it:

- **Anchors**: if a location's category matches a key in
  `felinni.geocode.DEFAULT_LOCATION_ANCHORS` (ships with `ucla` and
  `amgen` examples), that entry kicks in - the stored/displayed location
  is never touched, only the geocoding query (and, in the stronger form
  below, which results even count). Two forms:
  - a plain string (e.g. `"UCLA, Los Angeles, CA"`) is appended to the
    query as context - a nudge, not a guarantee, so a common building
    name (Royce Hall, Powell) can still occasionally lose to a
    same-named match elsewhere in the world.
  - `{"query":, "lat":, "lon":, "radius_km":}` does the same, plus
    hard-restricts the search to within that radius of `(lat, lon)` -
    guaranteed to land on campus rather than just biased toward it.
    Prefer this form (the shipped `ucla`/`amgen` anchors already use it)
    once you know your campus/workplace's coordinates.
  Edit that dict to match your own campus/workplace.
- **Region bias**: once ~5 locations in a run have resolved, later
  ambiguous queries are nudged (not restricted) toward that region -
  helps exactly the "well-known place, wrong match" case, automatically,
  for anyone.
- **Manual overrides**: for anything still wrong, copy
  `data/geocode_overrides.example.json` to `data/geocode_overrides.json`
  (gitignored) and add an exact `{"lat":, "lon":}` for that location
  string — overrides always win and never touch the network.

To fix a location that's already cached wrong, easiest is right from the
Map tab: the **Fix a location** card lets you pick the location and type a
corrected address, which is looked up once and saved as an override -
takes effect on the map immediately, no re-geocode run needed. The same
thing is also available by hand: add it to `geocode_overrides.json`, or
force just that entry to be re-geocoded:

```bash
python cli.py --events ../events.json geocode --clear "Santa Monica Pier" "B27 Terrace"
```

### Travel tab: metro areas and neighborhoods

The Travel tab groups nearby cities into one metro area (LA, Santa Monica,
and Pasadena all count as one trip's worth of geography, not three) rather
than listing every geocoded city separately. A metro's label defaults to
its most-visited city + country; to rename one to something more natural
("Bay Area", "Greater Toronto", "DC"), edit `felinni.regions.METRO_AREA_NAMES`.
The **Neighborhoods** card drills into any one metro area (defaulting to
home) for a finer breakdown - useful for wherever you actually live, e.g.
splitting "Los Angeles" into Downtown/West LA/the Valley/Orange County,
using Nominatim's neighborhood-level data where available.

### Category colors

If you've colored your calendars in Calendar.app, the exporter captures
each event's calendar color and the dashboard reuses those same colors
for the Map, the habit-activity strip, and the Time & Spend chart, instead
of an arbitrary fixed palette. Categories with no captured color (older
exports, or one set purely via a `Category:` note tag) fall back to the
fixed palette.

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
| Map every place, cluster by neighborhood, radius of life over time, places you stopped going to | `felinni.location` | Neighborhood clustering and radius-of-life need geocoded coordinates (`felinni.geocode`, opt-in, uses OpenStreetMap Nominatim, cached to disk). "Stopped going to" is CLI/library only (`cli.py stopped-going`) - not on the dashboard |
| Frequency of seeing people, growing/fading relationships, social time split | `felinni.social` | Needs attendees, `People:`/`With:` note tags, or a trailing "with A, B, and C" in the title. All-day events are excluded (same reasoning as seasonality/anomalies/spending below) - a full-day placeholder or multi-day trip block doesn't carry a real "time spent" the way a timed event does, and would otherwise inflate someone's hours. The dashboard's trend chart is switchable between year/month/week, with a dropdown checklist of everyone to plot (defaults to your top 6 checked) |
| Habit streaks/drop-offs, correlate with busy weeks | `felinni.habits` | Pass any category as the "habit" (Gym, Therapy, ...) |
| Repeating events falling off pace (Book Club, Poker Night, ...) | `felinni.recurring` | Auto-detects every named recurring series from Calendar's own repeat rule (`is_recurring`) - no need to pick one, unlike `felinni.habits` above. Flags each as active/slowing down/stopped relative to its own historical cadence |
| Trips away from home, metro areas visited, by geography | `felinni.regions` | Groups nearby cities (within ~80km) into one metro area, so a trip counts whether or not you tagged it - home is inferred as your most-visited metro. `neighborhoods_for_metro` gives a finer breakdown within any one metro (e.g. splitting "Los Angeles" into its neighborhoods) - on the dashboard, the Travel tab's Neighborhoods card |
| Travel timeline from tagged events only | `felinni.travel` | Secondary cross-check behind the above; collapses consecutive same-destination events into one trip |
| Time (and estimated spend) by category | `felinni.spending` | You supply the per-visit cost assumptions in `DEFAULT_COST_PER_VISIT` — nothing is invented; all-day events are excluded since they don't carry a real duration |
| Seasonality by month/season | `felinni.seasonality` | Only counts timed events — all-day entries (birthdays, holidays, vacations) are excluded |
| Unusually packed/empty weeks, in both directions and broken down by category | `felinni.anomalies` | Z-score on weekly scheduled hours (overall and per-category, each against its own baseline); all-day events excluded, same reasoning |
| Phases of Life (category phases), plus quiet stretches and home-base changes | `felinni.breaks` | Only "Phases of Life" (active category streaks) shows in the dashboard; the other two are available via `/api/breaks` |
| Link "first date" events to a Hinge/iMessage timestamp table | `felinni.dating_link` | Matches by tagged person + nearest timestamp within a configurable window |
| Suggested upcoming events from Eventbrite/Luma/Meetup | `felinni.future_events` | Skeleton only, not implemented - see "Future tab" above |

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
    calendar_sources.py        optional Google/Outlook/Apple ICS import, manifest + disk-cached per source
    future_events.py           skeleton only, not implemented - see "Future tab" above
    location.py, social.py, habits.py, travel.py, spending.py,
    seasonality.py, anomalies.py, breaks.py, recurring.py, regions.py, dating_link.py
  webapp/
    server.py                 Flask API wrapping felinni's analysis functions
    serialize.py               DataFrame -> JSON-safe records
    static/                    index.html, app.js, charts.js (hand-built SVG), styles.css
    static/vendor/leaflet/     vendored Leaflet (map tab), no CDN dependency
tests/
  make_sample_data.py        synthetic fixture generator
  test_analysis.py
  test_ingest.py             junk-location filtering, title-based people parsing, calendar colors
  test_breaks_and_trends.py  category anomalies, notable breaks, pandas-version-alias regressions
  test_geocode.py            anchors, region bias, overrides, failed-geocode retry
  test_calendar_sources.py   ICS parsing, source manifest add/sync/hide/delete
  test_future_events.py      pins down the Future tab skeleton's always-empty shape
  test_social.py             all-day events excluded from time-with-a-person/frequency
  test_regions.py            geographic-region grouping for the Travel tab
  test_recurring.py          repeating-event cadence/status detection
  test_webapp.py             smoke tests for the dashboard's API, incl. the geocode job
data/
  sample_events.json           generated fixture (committed for convenience)
  geocode_cache.json            built by `cli.py geocode`, gitignored (your location history stays local)
  geocode_overrides.example.json  committed template - copy to geocode_overrides.json (gitignored) to use it
  calendar_sources.json         imported-calendar manifest, gitignored
  sources/                      per-source cached events (one JSON file per import), gitignored
```
