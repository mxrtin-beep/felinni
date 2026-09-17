"""Skeleton for a "Future" tab: suggesting upcoming events worth going to,
pulled from Eventbrite/Luma/Meetup and ranked by fit with your own calendar
history (categories you're into, your usual day/time, places you already
frequent, people you tend to see).

None of this is implemented yet - each platform needs its own API access
wired in first (Eventbrite: API key, Luma: API key, Meetup: OAuth), which
isn't configured here. This module only defines the shape everything else
(webapp/server.py's /api/future, the Future tab's cards) is built against,
so plugging in a real integration later doesn't mean redesigning the tab.
"""
from __future__ import annotations

import pandas as pd

PLATFORMS = ("eventbrite", "luma", "meetup")


def platform_events(platform: str) -> list[dict]:
    """Would fetch upcoming events from `platform`'s API/search, normalized
    to {title, url, start, location, source}. Always empty until that
    platform's API access is wired in."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r} (expected one of {PLATFORMS})")
    return []


def suggestions_for(df: pd.DataFrame, candidate_events: list[dict] | None = None) -> list[dict]:
    """Would rank `candidate_events` (pulled from `platform_events` for
    each connected platform) by fit with `df` - category overlap, your
    usual day-of-week/time-of-day, places you already frequent, people you
    tend to see. Always empty for now: there's nothing to rank without a
    connected platform to pull candidates from."""
    return []
