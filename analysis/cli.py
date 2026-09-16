#!/usr/bin/env python3
"""felinni CLI: analyze an events.json export from CalendarExporter.

Usage:
    python cli.py places --events events.json
    python cli.py stopped-going --events events.json --min-visits 3 --inactive-months 9
    python cli.py people --events events.json
    python cli.py trends --events events.json
    python cli.py habit --events events.json --category Gym
    python cli.py travel --events events.json
    python cli.py time-by-category --events events.json
    python cli.py seasonality --events events.json --category Gym
    python cli.py anomalies --events events.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from felinni import anomalies, habits, ingest, location, seasonality, social, spending, travel


def _print(df_or_series, limit: int | None = 30):
    with pd.option_context("display.max_rows", limit, "display.width", 120):
        print(df_or_series)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", default="events.json", help="Path to CalendarExporter's JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("places", help="Visit frequency per location")

    p_stopped = sub.add_parser("stopped-going", help="Places you used to go and stopped")
    p_stopped.add_argument("--min-visits", type=int, default=3)
    p_stopped.add_argument("--inactive-months", type=int, default=9)

    sub.add_parser("people", help="Frequency and share of social time per person")
    sub.add_parser("trends", help="Growing vs. fading relationships by year")

    p_habit = sub.add_parser("habit", help="Streaks, consistency, and busyness correlation for a category")
    p_habit.add_argument("--category", required=True)

    sub.add_parser("travel", help="Trip timeline and places visited")
    sub.add_parser("time-by-category", help="Time (and estimated spend) by category")

    p_season = sub.add_parser("seasonality", help="Average activity by month/season")
    p_season.add_argument("--category", default=None)

    p_anom = sub.add_parser("anomalies", help="Unusually packed or empty weeks")
    p_anom.add_argument("--z-threshold", type=float, default=2.0)

    p_geo = sub.add_parser("geocode", help="Geocode every unique location via OpenStreetMap Nominatim (needs network; powers the dashboard's map)")
    p_geo.add_argument("--cache", default=None, help="Cache file path (default: data/geocode_cache.json)")

    args = parser.parse_args()
    df = ingest.load_events(args.events)
    if df.empty:
        print(f"No events loaded from {args.events}", file=sys.stderr)
        sys.exit(1)

    if args.command == "places":
        _print(location.place_frequency(df))
    elif args.command == "stopped-going":
        _print(location.places_you_stopped_going_to(df, args.min_visits, args.inactive_months))
    elif args.command == "people":
        _print(social.social_time_share(df))
    elif args.command == "trends":
        _print(social.fading_or_growing(df))
    elif args.command == "habit":
        weekly = habits.weekly_habit_counts(df, args.category)
        print("--- Streaks and gaps ---")
        _print(habits.streaks_and_gaps(weekly))
        print("\n--- Consistency by year ---")
        _print(habits.consistency_by_year(weekly))
        print("\n--- Correlation with busy weeks (negative = drops off when busy) ---")
        print(habits.habit_vs_busyness_correlation(df, args.category))
    elif args.command == "travel":
        print("--- Trips ---")
        _print(travel.trip_timeline(df))
        print("\n--- Places visited ---")
        _print(travel.places_visited(df))
    elif args.command == "time-by-category":
        _print(spending.estimated_spend_by_category(df))
    elif args.command == "seasonality":
        print("--- Monthly ---")
        _print(seasonality.monthly_activity(df, args.category))
        print("\n--- Seasonal ---")
        _print(seasonality.seasonal_activity(df, args.category))
    elif args.command == "anomalies":
        _print(anomalies.anomalous_weeks(df, args.z_threshold))
    elif args.command == "geocode":
        from felinni import geocode
        cache_path = args.cache or geocode.DEFAULT_CACHE_PATH
        unique_locations = df["location"].dropna().unique().tolist()
        location_categories = df.groupby("location")["category"].agg(lambda s: s.mode().iat[0]).to_dict()

        def on_progress(done, total):
            print(f"\rGeocoding via Nominatim (~1/sec, needs network): {done}/{total}", end="", flush=True)

        result = geocode.geocode_locations(
            unique_locations, cache_path=cache_path, on_progress=on_progress,
            location_categories=location_categories,
        )
        n_found = sum(1 for v in result.values() if v)
        print(f"\nDone: {n_found}/{len(unique_locations)} geocoded. Cache written to {cache_path}")


if __name__ == "__main__":
    main()
