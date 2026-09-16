"""Tests against the synthetic fixture in data/sample_events.json (see
make_sample_data.py for how it's generated and what patterns it encodes)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import anomalies, habits, ingest, location, seasonality, social, spending, travel

EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_events.json"


@pytest.fixture(scope="module")
def df():
    return ingest.load_events(EVENTS_PATH)


def test_load_events_schema(df):
    assert not df.empty
    for col in ["start", "end", "duration_hours", "category", "people", "year", "season"]:
        assert col in df.columns
    assert (df["end"] >= df["start"]).all()


def test_messy_event_survives_without_location_or_people(df):
    doctor = df[df["title"] == "Doctor appt"]
    assert len(doctor) == 1
    assert pd.isna(doctor.iloc[0]["location"])
    assert doctor.iloc[0]["people"] == []


def test_place_frequency_ranks_gyms_highest(df):
    freq = location.place_frequency(df)
    assert freq.index[0] in {
        "Equinox Union Square, 301 Grant Ave, San Francisco, CA 94108, United States",
        "Equinox SoMa, 747 Market St, San Francisco, CA 94103, United States",
        "Crunch - Mission",
    }
    assert (freq["visits"] > 0).all()


def test_places_you_stopped_going_to_flags_early_trips(df):
    stopped = location.places_you_stopped_going_to(df, min_visits=1, inactive_months=9)
    assert "Tokyo, Japan" in stopped.index


def test_person_frequency_and_share_sum_to_one(df):
    share = social.social_time_share(df)
    assert abs(share["share_of_social_hours"].sum() - 1.0) < 1e-9
    assert "Alice" in share.index


def test_habit_streaks_detect_gaps(df):
    weekly = habits.weekly_habit_counts(df, "Gym")
    streaks = habits.streaks_and_gaps(weekly)
    assert (streaks["active"] == False).any()  # noqa: E712
    assert (streaks["active"] == True).any()  # noqa: E712


def test_habit_correlation_is_negative_during_simulated_crunch(df):
    result = habits.habit_vs_busyness_correlation(df, "Gym")
    assert result["correlation"] is not None
    assert result["correlation"] < 0


def test_anomalies_catch_the_simulated_work_crunch(df):
    flagged = anomalies.anomalous_weeks(df, z_threshold=2.0)
    packed_weeks = flagged[flagged["label"] == "packed"]
    assert any(pd.Timestamp("2024-10-01") <= idx <= pd.Timestamp("2024-11-20") for idx in packed_weeks.index)


def test_travel_timeline_collapses_consecutive_events_into_trips(df):
    trips = travel.trip_timeline(df)
    assert len(trips) == 3
    assert set(trips["destination"]) == {"Tokyo, Japan", "Lisbon, Portugal", "New York, NY"}
    assert (trips["n_events"] == 3).all()


def test_seasonality_covers_all_four_seasons(df):
    seasonal = seasonality.seasonal_activity(df, "Gym")
    assert set(seasonal.index) == {"winter", "spring", "summer", "fall"}


def test_spending_reports_nan_for_uncosted_categories(df):
    spend = spending.estimated_spend_by_category(df, cost_per_visit={"gym": 0})
    social_row = spend[spend["category"] == "Social"].iloc[0]
    assert pd.isna(social_row["estimated_spend"])


def test_empty_events_file_loads_to_empty_frame(tmp_path):
    empty_path = tmp_path / "empty.json"
    empty_path.write_text("[]")
    result = ingest.load_events(empty_path)
    assert result.empty
