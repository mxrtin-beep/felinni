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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from felinni import anomalies, geocode, habits, ingest, seasonality, social, spending, travel, location
from webapp.serialize import records

app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent / "static"))
DF = None  # the full, unfiltered dataset - populated in main() before the server starts

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
    """The dataset for this request: DF narrowed to the global date-range
    filter every tab sends (start_date/end_date, both optional, inclusive).
    This is what makes the Overview date range apply everywhere - every
    other endpoint's own filters (category, person, ...) stack on top of it.
    """
    df = DF
    start_date = request.args.get("start_date")
    if start_date:
        df = df[df["start"] >= pd.Timestamp(start_date)]
    end_date = request.args.get("end_date")
    if end_date:
        df = df[df["start"] < pd.Timestamp(end_date) + pd.Timedelta(days=1)]
    return df


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


def _load_geocode_cache() -> dict:
    if geocode.DEFAULT_CACHE_PATH.exists():
        return json.loads(geocode.DEFAULT_CACHE_PATH.read_text())
    return {}


def _geocoded_count() -> int:
    cache = _load_geocode_cache()
    return sum(1 for v in cache.values() if v)


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
    geocoded = freq.dropna(subset=["lat", "lon"])

    return jsonify({
        "total_places": int(len(freq)),
        "geocoded_places": int(len(geocoded)),
        "locations": records(geocoded),
    })


def _run_geocode_job():
    def on_progress(done, total):
        with _geocode_lock:
            GEOCODE_STATE["done"] = done
            GEOCODE_STATE["total"] = total

    try:
        unique_locations = DF["location"].dropna().unique().tolist()
        geocode.geocode_locations(unique_locations, on_progress=on_progress)
    except Exception as e:
        with _geocode_lock:
            GEOCODE_STATE["error"] = str(e)
    finally:
        with _geocode_lock:
            GEOCODE_STATE["running"] = False


@app.post("/api/geocode/start")
def geocode_start():
    with _geocode_lock:
        if GEOCODE_STATE["running"]:
            return jsonify({"error": "already running"}), 409
        GEOCODE_STATE.update({"running": True, "done": 0, "total": 0, "error": None})
    threading.Thread(target=_run_geocode_job, daemon=True).start()
    return jsonify({"started": True})


@app.get("/api/geocode/status")
def geocode_status():
    with _geocode_lock:
        return jsonify(dict(GEOCODE_STATE))


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
    top_n = request.args.get("top_n", 6, type=int)
    granularity = request.args.get("granularity", "year")
    if granularity not in ("year", "month", "week"):
        return jsonify({"error": "granularity must be year, month, or week"}), 400
    df = _get_df()
    pivot = social.person_trend_by_period(df, granularity)
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


@app.get("/api/travel")
def travel_view():
    df = _get_df()
    trips = records(travel.trip_timeline(df))
    message = None
    if not trips:
        message = (
            "No events matched a travel category (Travel/Flight/Trip/Vacation/...) "
            "or a title like \"Flight to...\"/\"Trip to...\"/\"Vacation\". "
            "Tag trips with one of those categories, or via a Category: Travel note, "
            "to see them here."
        )
    return jsonify({
        "trips": trips,
        "places": records(travel.places_visited(df).reset_index()),
        "message": message,
    })


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
    return jsonify({"weekly": records(weekly), "anomalies": records(flagged)})


def main():
    global DF
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="../events.json", help="Path to CalendarExporter's JSON output")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    DF = ingest.load_events(args.events)
    if DF.empty:
        print(f"No events loaded from {args.events} - nothing to show.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(DF)} events from {args.events}. Serving on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
