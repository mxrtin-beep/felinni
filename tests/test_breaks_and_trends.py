"""Tests for felinni.breaks, felinni.anomalies.category_anomalies, and
felinni.social.person_trend_by_period - all added after the original
review round, covering the pandas-version-alias bug (resample("YE")/
Grouper(freq="Q") don't exist on every pandas 2.x/3.x) and the new
category-level/notable-breaks features."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import anomalies, breaks, ingest, social

EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_events.json"


@pytest.fixture(scope="module")
def df():
    return ingest.load_events(EVENTS_PATH)


def test_person_trend_by_period_totals_match_across_granularities(df):
    """Summing week-level counts for a person should equal their year-level
    total for the same year - the three code paths (week/month/year, each
    truncated differently) must agree on the underlying events."""
    yearly = social.person_trend_by_period(df, "year")
    weekly = social.person_trend_by_period(df, "week")
    person = yearly.columns[0]

    year = yearly.index[0]
    year_total = yearly.loc[year, person]
    week_total = weekly.loc[(weekly.index.year == year.year), person].sum()
    assert year_total == week_total


def test_person_trend_by_period_no_frequency_alias_errors(df):
    # This is really a regression test: resample("YE"/"ME") raise on
    # pandas < 2.2, and used to take down the whole People tab.
    for granularity in ["year", "month", "week"]:
        result = social.person_trend_by_period(df, granularity)
        assert not result.empty


def test_category_anomalies_flags_both_directions(df):
    result = anomalies.category_anomalies(df, z_threshold=2.0)
    assert not result.empty
    assert set(result["label"].unique()) <= {"packed", "empty"}
    assert "Work" in result["category"].values  # the simulated work crunch


def test_category_anomalies_skips_low_activity_categories(df):
    result = anomalies.category_anomalies(df, z_threshold=2.0, min_active_weeks=4)
    # "Date" and "Personal" each occur exactly once in the fixture - too
    # little data for a meaningful z-score, so they should never appear.
    assert "Date" not in result["category"].values
    assert "Personal" not in result["category"].values


def test_category_phases_detects_long_active_streaks(df):
    result = breaks.category_phases(df, min_weeks=8)
    assert not result.empty
    assert set(result["type"].unique()) <= {"active", "break"}
    assert (result["weeks"] >= 8).all()


def test_location_shifts_runs_without_frequency_alias_errors(df):
    # Regression test for Grouper(freq="Q") - removed in pandas 3.0.
    result = breaks.location_shifts(df)
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["quarter", "location", "visits"] or result.empty


def test_quiet_stretches_returns_expected_columns(df):
    result = breaks.quiet_stretches(df)
    assert isinstance(result, pd.DataFrame)
    assert set(result.columns) <= {"start", "end", "weeks"}
