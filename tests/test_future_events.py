"""Tests for the felinni.future_events skeleton - everything here is a
placeholder (see the module docstring), so these just pin down the shape:
always empty, and platform_events rejects an unknown platform name."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import future_events


@pytest.mark.parametrize("platform", future_events.PLATFORMS)
def test_platform_events_is_empty_for_every_known_platform(platform):
    assert future_events.platform_events(platform) == []


def test_platform_events_rejects_unknown_platform():
    with pytest.raises(ValueError):
        future_events.platform_events("carrier-pigeon")


def test_suggestions_for_is_empty():
    assert future_events.suggestions_for(pd.DataFrame()) == []
