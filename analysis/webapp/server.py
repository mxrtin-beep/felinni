#!/usr/bin/env python3
"""felinni dashboard: a local Flask server exposing the felinni analysis
functions as JSON, plus the static single-page frontend in static/.

    python3 webapp/server.py --events ../events.json
    open http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from felinni import anomalies, breaks, calendar_sources, future_events, geocode, habits, ingest, recurring, regions, seasonality, social, spending, travel, location
from webapp.serialize import records

app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent / "static"))
DF = None  # the full, unfiltered dataset - populated in main() before the server starts
EVENTS_PATH = None  # the primary --events file, merged with imported calendar sources on every reload

_geocode_lock = threading.Lock()
GEOCODE_STATE = {"running": False, "done": 0, "total": 0, "error": None}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/<path:filename>")
def static_assets(filename):
    # Only ever serves files that live in static/ (charts.js, app.js, styles.css,
    # vendor/leaflet/...) - /api/* is matched first since Flask prefers the
    # more specific rule.
    return send_from_directory(app.static_folder, filename)


def _get_df() -> pd.DataFrame:
    """The dataset for this request: DF narrowed to the global filters every
    tab sends - the date range (start_date/end_date, inclusive) and any
    categories unchecked in the Overview's category list
    (exclude_categories, comma-separated). This is what makes those two
    controls apply everywhere - every other endpoint's own filters
    (a habit's category, the Map's person/year filters, ...) stack on top.
    """
    df = DF
    start_date = request.args.get("start_date")
    if start_date:
        df = df[df["start"] >= pd.Timestamp(start_date)]
    end_date = request.args.get("end_date")
    if end_date:
        df = df[df["start"] < pd.Timestamp(end_date) + pd.Timedelta(days=1)]
    exclude_categories = request.args.get("exclude_categories")
    if exclude_categories:
        excluded = {c.casefold() for c in exclude_categories.split(",") if c}
        df = df[~df["category"].str.casefold().isin(excluded)]
    return df


def _load_primary_events() -> list[dict]:
    if not EVENTS_PATH:
        return []
    path = Path(EVENTS_PATH)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _reload_dataset() -> None:
    """Rebuilds DF from the primary events.json (if any) plus every
    visible imported calendar source - called after any change to the
    source manifest (add/sync/hide/delete) and by the background poller."""
    global DF
    combined = _load_primary_events() + calendar_sources.merged_source_events(
        calendar_sources.DEFAULT_MANIFEST_PATH, calendar_sources.DEFAULT_SOURCES_DIR,
    )
    combined = calendar_sources.dedupe_events(combined)
    DF = ingest.load_events_from_records(combined)


@app.get("/api/meta")
def meta():
    # Always the full, unfiltered dataset - this populates the date pickers
    # and filter dropdowns, whose bounds shouldn't shrink as you filter.
    return jsonify({
        "total_events": int(len(DF)),
        "min_year": int(DF["year"].min()),
        "max_year": int(DF["year"].max()),
        "min_date": DF["start"].min().date().isoformat(),
        "max_date": DF["start"].max().date().isoformat(),
        "categories": sorted(DF["category"].dropna().unique().tolist(), key=lambda c: -int((DF["category"] == c).sum())),
        "people": sorted(DF[DF["n_people"] > 0].explode("people")["people"].dropna().unique().tolist()),
        "n_geocoded": _geocoded_count(),
        "category_colors": ingest.category_color_map(DF),
    })


@app.get("/api/summary")
def summary():
    df = _get_df()
    return jsonify({
        "total_events": int(len(df)),
        "total_hours": float(df["duration_hours"].sum()),
        "top_category": df["category"].value_counts().idxmax() if len(df) else None,
        "n_people": int(df[df["n_people"] > 0].explode("people")["people"].nunique()) if len(df) else 0,
    })


@app.get("/api/breaks")
def breaks_view():
    df = _get_df()
    return jsonify({
        "category_phases": records(breaks.category_phases(df)),
        "quiet_stretches": records(breaks.quiet_stretches(df)),
        "location_shifts": records(breaks.location_shifts(df)),
    })


def _load_geocode_cache() -> dict:
    return geocode.effective_cache(geocode.DEFAULT_CACHE_PATH, geocode.DEFAULT_OVERRIDES_PATH)


def _geocoded_count() -> int:
    cache = _load_geocode_cache()
    return sum(1 for v in cache.values() if v)


def _location_titles(events, top_n: int = 6) -> dict:
    """Location -> a short list of the event titles seen there (most
    frequent first, "(3x)" suffix for repeats), so the map popup can show
    what actually happens at a place, not just its visit count."""
    located = events.dropna(subset=["location"])
    located = located[located["location"].str.strip() != ""]
    out = {}
    for loc, group in located.groupby("location"):
        counts = group["title"].value_counts()
        out[loc] = [
            f"{title} ({count}×)" if count > 1 else title
            for title, count in counts.head(top_n).items()
        ]
    return out


@app.get("/api/places")
def places():
    limit = request.args.get("limit", 20, type=int)
    freq = location.place_frequency(_get_df()).reset_index().head(limit)
    return jsonify(records(freq))


@app.get("/api/locations")
def locations_view():
    """Geocoded location frequency, filterable by category/person/year range
    on top of the global date filter, for the map tab. Only locations
    present in the geocode cache (built via the Map tab's Geocode button, or
    `python cli.py geocode`) come back with lat/lon; everything else is
    still counted in `total_places` so the UI can say how much is missing.
    """
    events = _get_df()
    category = request.args.get("category")
    if category:
        events = events[events["category"].str.casefold() == category.casefold()]
    person = request.args.get("person")
    if person:
        events = events[events["people"].apply(lambda people: person in people)]
    start_year = request.args.get("start_year", type=int)
    if start_year:
        events = events[events["year"] >= start_year]
    end_year = request.args.get("end_year", type=int)
    if end_year:
        events = events[events["year"] <= end_year]

    freq = location.place_frequency(events).reset_index()
    cache = _load_geocode_cache()
    freq["lat"] = freq["location"].map(lambda loc: (cache.get(loc) or {}).get("lat"))
    freq["lon"] = freq["location"].map(lambda loc: (cache.get(loc) or {}).get("lon"))
    freq["display_name"] = freq["location"].map(lambda loc: (cache.get(loc) or {}).get("display_name"))
    titles_by_location = _location_titles(events)
    freq["titles"] = freq["location"].map(lambda loc: titles_by_location.get(loc, []))
    geocoded = freq.dropna(subset=["lat", "lon"])

    return jsonify({
        "total_places": int(len(freq)),
        "geocoded_places": int(len(geocoded)),
        "locations": records(geocoded),
        # Every distinct location string (geocoded or not), for the "fix a
        # location" picker - you should be able to recode one that failed
        # to geocode at all, not just one that resolved somewhere wrong.
        "all_locations": sorted(freq["location"].tolist()),
    })


def _run_geocode_job(force: bool = False):
    def on_progress(done, total):
        with _geocode_lock:
            GEOCODE_STATE["done"] = done
            GEOCODE_STATE["total"] = total

    try:
        unique_locations = DF["location"].dropna().unique().tolist()
        location_categories = DF.groupby("location")["category"].agg(lambda s: s.mode().iat[0]).to_dict()
        geocode.geocode_locations(unique_locations, on_progress=on_progress, location_categories=location_categories, force=force)
    except Exception as e:
        with _geocode_lock:
            GEOCODE_STATE["error"] = str(e)
    finally:
        with _geocode_lock:
            GEOCODE_STATE["running"] = False


@app.post("/api/geocode/start")
def geocode_start():
    payload = request.get_json(force=True, silent=True) or {}
    force = bool(payload.get("force"))
    with _geocode_lock:
        if GEOCODE_STATE["running"]:
            return jsonify({"error": "already running"}), 409
        GEOCODE_STATE.update({"running": True, "done": 0, "total": 0, "error": None})
    threading.Thread(target=_run_geocode_job, args=(force,), daemon=True).start()
    return jsonify({"started": True, "force": force})


@app.get("/api/geocode/status")
def geocode_status():
    with _geocode_lock:
        return jsonify(dict(GEOCODE_STATE))


@app.get("/api/geocode/failures")
def geocode_failures():
    """Why each currently-unresolved location failed on its last attempt
    (see felinni.geocode.geocode_locations) - a timeout, a Nominatim
    service error (often a rate limit/temporary block), or a genuine "no
    match" - grouped so a systemic problem (most failures share one reason)
    is obvious at a glance instead of just a bare "N not geocoded" count.
    `approximate` is a separate, less alarming list: locations that DO have
    a pin (placed at a campus/workplace anchor's own coordinates, e.g.
    "Boelter 5800" at "UCLA, Los Angeles, CA") because their own address
    couldn't be resolved, not a failure to report on. Only locations still
    in the current dataset are included in either list, in case the
    underlying file has entries from a since-changed events export."""
    current_locations = set(DF["location"].dropna().unique().tolist())

    diagnostics = geocode.load_diagnostics(geocode.DEFAULT_DIAGNOSTICS_PATH)
    relevant = {loc: reason for loc, reason in diagnostics.items() if loc in current_locations}

    by_reason: dict[str, int] = {}
    for reason in relevant.values():
        key = reason.split(" (query:")[0]
        by_reason[key] = by_reason.get(key, 0) + 1

    failures = [{"location": loc, "reason": reason} for loc, reason in sorted(relevant.items())]

    approximations = geocode.load_approximations(geocode.DEFAULT_APPROXIMATIONS_PATH)
    approximate = [
        {"location": loc, "placed_at": placed_at}
        for loc, placed_at in sorted(approximations.items())
        if loc in current_locations
    ]

    return jsonify({
        "total_failed": len(failures),
        "by_reason": sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True),
        "failures": failures,
        "approximate": approximate,
    })


@app.post("/api/geocode/override")
def geocode_override():
    """Recode a location from the Map tab: given the exact location string
    and either a corrected address to look up (`query`) or an exact
    `lat`/`lon`, saves it to `geocode_overrides.json`, which always wins
    over the cache/network on every future run and takes effect on the map
    immediately (no re-geocode needed)."""
    payload = request.get_json(force=True, silent=True) or {}
    location_str = (payload.get("location") or "").strip()
    if not location_str:
        return jsonify({"error": "location is required"}), 400

    if isinstance(payload.get("lat"), (int, float)) and isinstance(payload.get("lon"), (int, float)):
        entry = {"lat": payload["lat"], "lon": payload["lon"], "display_name": payload.get("display_name") or location_str}
    else:
        query = (payload.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query or lat/lon is required"}), 400
        try:
            entry = geocode.geocode_one(query)
        except ImportError as e:
            return jsonify({"error": str(e)}), 500
        if not entry:
            return jsonify({"error": f"couldn't find a match for {query!r}"}), 404

    geocode.save_override(location_str, entry, geocode.DEFAULT_OVERRIDES_PATH)
    geocode.clear_diagnostics_entry(location_str, geocode.DEFAULT_DIAGNOSTICS_PATH)
    geocode.clear_approximation_entry(location_str, geocode.DEFAULT_APPROXIMATIONS_PATH)
    return jsonify({"location": location_str, "entry": entry})


@app.get("/api/sources")
def list_sources():
    return jsonify(calendar_sources.list_sources(calendar_sources.DEFAULT_MANIFEST_PATH))


@app.post("/api/sources")
def add_source():
    """Registers an imported calendar - either an ICS-link source (JSON
    body: name/provider/kind="ics_url"/url) or a file upload (multipart
    form: name/provider/kind="ics_file"|"events_json" fields + a `file`).
    Either way, syncs immediately and merges it into the working dataset."""
    is_json = request.content_type and "application/json" in request.content_type
    payload = request.get_json(silent=True) or {} if is_json else {}
    name = (request.form.get("name") or payload.get("name") or "").strip()
    provider = request.form.get("provider") or payload.get("provider")
    kind = request.form.get("kind") or payload.get("kind")
    url = request.form.get("url") or payload.get("url")
    file_bytes = request.files["file"].read() if "file" in request.files else None

    if not name or not provider or not kind:
        return jsonify({"error": "name, provider, and kind are required"}), 400

    try:
        entry = calendar_sources.add_source(
            name, provider, kind, url=url, file_bytes=file_bytes,
            manifest_path=calendar_sources.DEFAULT_MANIFEST_PATH, sources_dir=calendar_sources.DEFAULT_SOURCES_DIR,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _reload_dataset()
    return jsonify(entry)


@app.post("/api/sources/<source_id>/sync")
def sync_source(source_id):
    try:
        entry = calendar_sources.sync_source(
            source_id, calendar_sources.DEFAULT_MANIFEST_PATH, calendar_sources.DEFAULT_SOURCES_DIR,
        )
    except KeyError:
        return jsonify({"error": "no such source"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _reload_dataset()
    return jsonify(entry)


@app.patch("/api/sources/<source_id>")
def update_source(source_id):
    payload = request.get_json(force=True, silent=True) or {}
    if "visible" not in payload:
        return jsonify({"error": "visible is required"}), 400
    entry = calendar_sources.set_visibility(source_id, bool(payload["visible"]), calendar_sources.DEFAULT_MANIFEST_PATH)
    if entry is None:
        return jsonify({"error": "no such source"}), 404
    _reload_dataset()
    return jsonify(entry)


@app.delete("/api/sources/<source_id>")
def delete_source(source_id):
    if not calendar_sources.remove_source(
        source_id, calendar_sources.DEFAULT_MANIFEST_PATH, calendar_sources.DEFAULT_SOURCES_DIR,
    ):
        return jsonify({"error": "no such source"}), 404
    _reload_dataset()
    return jsonify({"deleted": True})


@app.get("/api/stopped-going")
def stopped_going():
    min_visits = request.args.get("min_visits", 3, type=int)
    inactive_months = request.args.get("inactive_months", 9, type=int)
    df = location.places_you_stopped_going_to(_get_df(), min_visits, inactive_months).reset_index()
    return jsonify(records(df))


@app.get("/api/people")
def people():
    limit = request.args.get("limit", 20, type=int)
    df = social.social_time_share(_get_df()).reset_index().head(limit)
    return jsonify(records(df))


@app.get("/api/trends")
def trends():
    return jsonify(records(social.fading_or_growing(_get_df())))


@app.get("/api/person-trend")
def person_trend():
    granularity = request.args.get("granularity", "year")
    if granularity not in ("year", "month", "week"):
        return jsonify({"error": "granularity must be year, month, or week"}), 400
    df = _get_df()
    pivot = social.person_trend_by_period(df, granularity)
    # `people` (explicit, comma-separated, possibly empty) lets the
    # dashboard's checkbox picker show exactly who was asked for -
    # including nobody, if every checkbox is unchecked. Omitting the param
    # entirely falls back to the original top-N-by-events default.
    if "people" in request.args:
        top_people = [p.strip() for p in request.args.get("people", "").split(",") if p.strip()]
    else:
        top_n = request.args.get("top_n", 6, type=int)
        top_people = social.person_frequency(df).head(top_n).index.tolist()
    rows = []
    for person in top_people:
        if person not in pivot.columns:
            continue
        for period, count in pivot[person].items():
            rows.append({"person": person, "period": period.date().isoformat(), "count": int(count)})
    return jsonify(rows)


@app.get("/api/habit")
def habit():
    category = request.args.get("category")
    if not category:
        return jsonify({"error": "category query param required"}), 400
    df = _get_df()
    weekly = habits.weekly_habit_counts(df, category)
    weekly_records = [{"week": ts.isoformat(), "count": int(c)} for ts, c in weekly.items()]
    return jsonify({
        "weekly": weekly_records,
        "streaks": records(habits.streaks_and_gaps(weekly)),
        "consistency": records(habits.consistency_by_year(weekly)),
        "correlation": habits.habit_vs_busyness_correlation(df, category),
    })


@app.get("/api/recurring")
def recurring_view():
    df = _get_df()
    return jsonify(records(recurring.recurring_series(df)))


@app.get("/api/travel")
def travel_view():
    df = _get_df()
    cache = _load_geocode_cache()

    region_visits = regions.visits_by_region(df, cache)
    region_trips = records(regions.trips_away_from_home(df, cache))
    home = regions.home_region(region_visits)

    message = None
    if not cache:
        message = (
            "No locations geocoded yet, so trips can't be grouped by region. "
            "Click \"Geocode locations\" on the Map tab first."
        )

    # A secondary, tag-based view: catches trips tagged Travel/Flight/... in
    # your calendar even if their location isn't geocoded (or at all, if
    # you haven't geocoded anything yet).
    tagged_trips = records(travel.trip_timeline(df))

    return jsonify({
        "home_region": home,
        "region_visits": records(region_visits.reset_index()),
        "region_trips": region_trips,
        "tagged_trips": tagged_trips,
        "message": message,
    })


@app.get("/api/travel/neighborhoods")
def travel_neighborhoods():
    """Finer breakdown within one metro area - defaults to home if `metro`
    isn't given - for the Travel tab's drill-down (e.g. LA split into
    Downtown/West LA/the Valley/Orange County instead of stopping at "Los
    Angeles" as one blob)."""
    df = _get_df()
    cache = _load_geocode_cache()
    region_visits = regions.visits_by_region(df, cache)
    metro = request.args.get("metro") or regions.home_region(region_visits)
    if not metro:
        return jsonify({"metro": None, "neighborhoods": []})
    neighborhoods = regions.neighborhoods_for_metro(df, cache, metro)
    return jsonify({"metro": metro, "neighborhoods": records(neighborhoods.reset_index())})


@app.get("/api/time-by-category")
def time_by_category():
    return jsonify(records(spending.estimated_spend_by_category(_get_df())))


@app.get("/api/seasonality")
def seasonality_view():
    category = request.args.get("category") or None
    df = _get_df()
    return jsonify({
        "monthly": records(seasonality.monthly_activity(df, category)),
        "seasonal": records(seasonality.seasonal_activity(df, category)),
    })


@app.get("/api/anomalies")
def anomalies_view():
    z = request.args.get("z", 2.0, type=float)
    df = _get_df()
    weekly = anomalies.weekly_load(df).reset_index().rename(columns={"index": "week"})
    flagged = anomalies.anomalous_weeks(df, z).reset_index().rename(columns={"index": "week"})
    by_category = records(anomalies.category_anomalies(df, z))
    return jsonify({"weekly": records(weekly), "anomalies": records(flagged), "by_category": by_category})


@app.get("/api/future")
def future_view():
    """Future tab: events found for Eventbrite/Luma/Meetup/Camber/Partiful/
    Posh plus anywhere else DuckDuckGo turns up, via a DuckDuckGo search,
    enriched with start/end/duration/location from each event's own page
    (see felinni.future_events - no platform API key/OAuth needed).

    Uses the full, unfiltered dataset (not `_get_df()`) for ranking/
    conflict-checking - your habits and existing calendar are what matter
    here, regardless of whatever date range the Overview tab's global
    filter happens to be set to; that filter has nothing to do with a
    forward-looking event search.

    `region` defaults to your geocoded home metro, falling back to
    felinni.future_events.DEFAULT_REGION if nothing's been geocoded yet;
    `?region=` overrides either for a one-off search elsewhere. `?days=`
    (default 7) bounds how far ahead to look - both nudging the search
    itself toward near-term results and actually dropping anything whose
    confirmed date falls outside that window (see
    felinni.future_events.within_search_window). `events` is one ranked
    list - every candidate scored by fit with your calendar history and
    flagged with any scheduling conflicts - rather than a separate
    "suggested" list duplicating the same events in a different order. If a
    local Ollama server is running, a few AI-brainstormed (clearly
    labeled, not real listings) event ideas are folded in too."""
    df = DF
    cache = _load_geocode_cache()
    home = regions.home_region(regions.visits_by_region(df, cache))
    region = request.args.get("region") or home or future_events.DEFAULT_REGION
    days = request.args.get("days", future_events.DEFAULT_SEARCH_WINDOW_DAYS, type=int)

    raw_events = []
    for i, platform in enumerate(future_events.PLATFORMS):
        if i > 0:
            time.sleep(0.5)  # a small gap between platforms - back off DuckDuckGo's rate limiting a bit
        raw_events.extend(future_events.platform_events(platform, region=region, days_ahead=days))
    time.sleep(0.5)
    raw_events.extend(future_events.other_web_events(region=region, days_ahead=days))
    raw_events.extend(future_events.ollama_event_ideas(df, region=region))

    windowed = future_events.within_search_window(raw_events, days_ahead=days)
    annotated = future_events.annotate_conflicts(windowed, df)
    events = future_events.suggestions_for(df, annotated)

    message = None
    if not events:
        message = (
            f"No results from Eventbrite/Luma/Meetup/Camber/other sites for \"{region}\" right now - "
            "try a different region, or DuckDuckGo may be rate-limiting this search."
        )

    return jsonify({
        "region": region,
        "days": days,
        "events": events,
        "message": message,
    })


def _background_sync_loop(interval_seconds: float) -> None:
    """Re-fetches every ICS-link calendar source every `interval_seconds`
    for as long as this process runs, so a Google/Outlook import stays
    reasonably fresh without a manual "Refresh now" click each time. Not a
    true always-on sync - it only runs while the dev server is up."""
    while True:
        time.sleep(interval_seconds)
        try:
            if calendar_sources.sync_all_url_sources(calendar_sources.DEFAULT_MANIFEST_PATH, calendar_sources.DEFAULT_SOURCES_DIR):
                _reload_dataset()
        except Exception as e:
            print(f"Background calendar sync failed: {e}", file=sys.stderr)


def main():
    global EVENTS_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="../events.json", help="Path to CalendarExporter's JSON output")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--sync-interval-minutes", type=float, default=30,
        help="How often to re-fetch imported ICS-link calendars while the server is running (0 disables polling)",
    )
    args = parser.parse_args()

    EVENTS_PATH = args.events
    _reload_dataset()
    if DF.empty:
        print(
            f"No events loaded from {args.events} or any imported calendar source - nothing to show.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.sync_interval_minutes > 0:
        threading.Thread(target=_background_sync_loop, args=(args.sync_interval_minutes * 60,), daemon=True).start()

    print(f"Loaded {len(DF)} events. Serving on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
