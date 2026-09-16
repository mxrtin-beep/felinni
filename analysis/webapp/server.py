#!/usr/bin/env python3
"""felinni dashboard: a local Flask server exposing the felinni analysis
functions as JSON, plus the static single-page frontend in static/.

    python3 webapp/server.py --events ../events.json
    open http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from felinni import anomalies, habits, ingest, social, spending, travel, location
from webapp.serialize import records

app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent / "static"))
DF = None  # populated in main() before the server starts


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/<path:filename>")
def static_assets(filename):
    # Only ever serves files that live in static/ (charts.js, app.js, styles.css) -
    # /api/* is matched first since Flask prefers the more specific rule.
    return send_from_directory(app.static_folder, filename)


@app.get("/api/meta")
def meta():
    return jsonify({
        "total_events": int(len(DF)),
        "total_hours": float(DF["duration_hours"].sum()),
        "min_year": int(DF["year"].min()),
        "max_year": int(DF["year"].max()),
        "categories": sorted(DF["category"].dropna().unique().tolist(), key=lambda c: -int((DF["category"] == c).sum())),
        "n_people": int(DF[DF["n_people"] > 0].explode("people")["people"].nunique()),
    })


@app.get("/api/places")
def places():
    limit = request.args.get("limit", 20, type=int)
    freq = location.place_frequency(DF).reset_index().head(limit)
    return jsonify(records(freq))


@app.get("/api/stopped-going")
def stopped_going():
    min_visits = request.args.get("min_visits", 3, type=int)
    inactive_months = request.args.get("inactive_months", 9, type=int)
    df = location.places_you_stopped_going_to(DF, min_visits, inactive_months).reset_index()
    return jsonify(records(df))


@app.get("/api/people")
def people():
    limit = request.args.get("limit", 20, type=int)
    df = social.social_time_share(DF).reset_index().head(limit)
    return jsonify(records(df))


@app.get("/api/trends")
def trends():
    return jsonify(records(social.fading_or_growing(DF)))


@app.get("/api/person-year-trend")
def person_year_trend():
    top_n = request.args.get("top_n", 6, type=int)
    pivot = social.person_trend_by_year(DF)
    top_people = social.person_frequency(DF).head(top_n).index.tolist()
    rows = []
    for person in top_people:
        if person not in pivot.index:
            continue
        for year, count in pivot.loc[person].items():
            rows.append({"person": person, "year": int(year), "count": int(count)})
    return jsonify(rows)


@app.get("/api/habit")
def habit():
    category = request.args.get("category")
    if not category:
        return jsonify({"error": "category query param required"}), 400
    weekly = habits.weekly_habit_counts(DF, category)
    weekly_records = [{"week": ts.isoformat(), "count": int(c)} for ts, c in weekly.items()]
    return jsonify({
        "weekly": weekly_records,
        "streaks": records(habits.streaks_and_gaps(weekly)),
        "consistency": records(habits.consistency_by_year(weekly)),
        "correlation": habits.habit_vs_busyness_correlation(DF, category),
    })


@app.get("/api/travel")
def travel_view():
    return jsonify({
        "trips": records(travel.trip_timeline(DF)),
        "places": records(travel.places_visited(DF).reset_index()),
    })


@app.get("/api/time-by-category")
def time_by_category():
    return jsonify(records(spending.estimated_spend_by_category(DF)))


@app.get("/api/seasonality")
def seasonality_view():
    from felinni import seasonality
    category = request.args.get("category") or None
    return jsonify({
        "monthly": records(seasonality.monthly_activity(DF, category)),
        "seasonal": records(seasonality.seasonal_activity(DF, category)),
    })


@app.get("/api/anomalies")
def anomalies_view():
    z = request.args.get("z", 2.0, type=float)
    weekly = anomalies.weekly_load(DF).reset_index().rename(columns={"index": "week"})
    flagged = anomalies.anomalous_weeks(DF, z).reset_index().rename(columns={"index": "week"})
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
