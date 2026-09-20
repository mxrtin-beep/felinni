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

- **People** — one table per person consolidating time spent (a mini
  bar), time since you last saw them (a ring that fills and shifts from
  green to red the longer it's been), known since (a mini bar off their
  earliest event, e.g. "8mo" or "2.3yr"), and growing/fading (a small
  diverging bar, green/right for more often, red/left for less), plus an
  "events over time" chart with a dropdown for who to plot and a
  draggable range slider to zoom into a sub-range. All-day events are
  excluded everywhere hours are counted, so a full-day placeholder
  doesn't inflate anyone's total. A friend network graph plots everyone
  as a node, linked to whoever they've shared a real, timed event with
  (thicker link = more shared events, draggable to rearrange), scroll/pinch
  to zoom and drag the background to pan. Node color is switchable via a
  dropdown between time since last seen, known since, recent trend, or
  time spent together, with a legend for whichever is selected.
- **Habits & repeating events** — streaks/gaps for any category you pick,
  plus auto-detected recurring series (Book Club, Standup, ...) flagged
  active/slowing down/stopped against their own historical cadence.
  Detected from a repeated title, not Calendar's own repeat-rule flag, so
  a habit typed in fresh each time (a gym rotation like "Push Day"/"Pull
  Day") is still picked up; a trailing "with A, B" guest list is stripped
  first so "Dinner with Alice" and "Dinner with Bob" count as the same
  series instead of two one-offs.

  ![Habits tab](docs/images/habits.png)
- **Map & Travel** — click **Geocode locations** to plot everything
  (OpenStreetMap Nominatim, cached to disk, ~1/sec). Nearby cities group
  into one metro area for trip-counting, with a neighborhood drill-down for
  your home area. Wrong pin? Fix it inline from the **Fix a location** card
  — no re-geocoding needed. A location that can't be resolved on its own
  but matches a campus/workplace anchor with known coordinates (see
  `DEFAULT_LOCATION_ANCHORS` in `felinni/geocode.py`) is placed there
  instead of left off the map — e.g. "Boelter 5800" (a UCLA room number,
  not its own addressable point) lands at "UCLA, Los Angeles, CA." Before
  giving up on an address, a few common calendar-export quirks are fixed
  automatically: embedded newlines/extra whitespace, a missing comma
  between street and city, a business name glued directly onto its own
  house number ("101 Boxing Club 1714 Newbury Rd..." retries as "1714
  Newbury Rd..."), a directional qualifier after the street suffix kept
  with the street instead of getting absorbed into the city ("...4th St
  NW Washington DC..." -> "...St NW, Washington, DC..." rather than
  city "NW Washington") and, if that alone doesn't resolve it, dropped
  from the street entirely ("...4th St NW..." -> "...4th St..." — Nominatim
  often doesn't index the abbreviated direction at all), and a unit/suite/apartment/floor/room clause dropped
  ("...Ave, Unit 1420, Los Angeles..." retries as "...Ave, Los
  Angeles..."). If the street address still won't resolve at all, a
  landmark's own name plus its city is tried on its own ("Balboa Park 1549
  El Prado, San Diego..." retries as "Balboa Park, San Diego") - some
  parks/campuses/plazas are indexed by name rather than mailing address.
  A handful of well-known Los Angeles-area neighborhoods used as the
  mailing city (Van Nuys, Pacific Palisades, Woodland Hills, ...) are also
  retried against "Los Angeles" itself, since they're not their own
  incorporated city. Anything that still fails, or was only placed
  approximately, shows up with its reason in the **Geocoding notes** card,
  instead of a bare "N not geocoded" count with no way to tell why.
- **Import calendars** (Overview tab) — pull in Google/Outlook/a second
  Apple calendar via their "secret ICS link" (no OAuth), or upload a file.
  ICS-link sources refresh automatically every 30 min while the server
  runs. Duplicate events across sources are matched and only counted once.
- **Phases of Life** (Overview tab) — a Gantt-style timeline of which
  category was consistently active when.
- **Future** — doesn't search automatically on load or on a global
  filter change; it's a live external web search (~7 sources, easily
  20-40+ seconds), not a query over your own already-loaded calendar
  data, so it only runs once you click **Search** (or change the region/
  window) on that tab. One ranked list of upcoming events from Eventbrite, Luma,
  Meetup, Camber (the "LA Happenings" newsletter), Partiful, Posh, and
  anywhere else a web search turns up for your home region (or any region
  you type in) within the next 7 days by default (7/14/30/90 selectable) —
  narrower than "everything upcoming forever," which tends to surface
  far-future festivals over what's actually happening soon. Searches ~7
  sources one at a time (with a brief pause between each, to stay
  polite), so a status bar shows which source it's on and how far along
  it is rather than one long unexplained wait. Each source is searched
  twice and the results merged - the underlying search library shuffles
  which of its backend engines actually get consulted per call, so the
  same query can turn up a different (or empty) set of results from one
  run to the next; repeating it evens that out at the cost of the search
  taking a bit longer.
  Enriched with start/end time, duration, and location straight from each
  event's own page — no API key/OAuth needed. (Camber's a Substack roundup
  rather than a per-event platform, so its results usually keep their date
  unknown — there's no event page to enrich them from.)
  Skips browse/listing pages ("Discover LA Events", a platform's own
  homepage or account page) in favor of actual single events, picks the
  soonest occurrence for a "multiple dates" listing rather than blending
  across them, dedupes the same real event when it's independently
  listed on more than one aggregator site, and flags anything that
  overlaps your calendar or another suggestion. Ranks by fit with your
  habits (category, usual day/time, familiar venues) and suggests people
  to invite with a reason, weighted toward your *recent* history first —
  who you usually do that category/venue with lately, or your most
  frequent people recently as a last resort, so someone you saw
  constantly a while back but haven't since doesn't keep outranking who
  you're actually spending time with now. The last suggested person is
  reserved as a reconnection nudge — whoever you have a real history with
  but haven't seen in the longest time — blended in alongside your usual
  company rather than instead of it. If a local Ollama server is
  running, a few clearly-labeled AI-brainstormed event ideas are folded
  in too — never presented as a real listing.

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
