"""Reconnect suggestions: who to invite to events already sitting on your
calendar in the next two weeks.

Narrowed to people you genuinely haven't seen in a while - specifically,
within MAX_DAYS_SINCE_SEEN (a flat threshold rather than a ratio against
their own usual cadence - a ratio makes a rare, one-off contact from years
ago look permanently "overdue" off a single data point). This is a
ceiling, not a floor: someone you haven't shared an event with in a
decade has likely drifted out of your life for a reason and isn't a
useful "reconnect" suggestion, so they're excluded entirely rather than
topping the list just for being the most overdue. Further narrowed to
events that actually have a location (nothing to match a person's usual
hangout region against otherwise), to people you've actually hung out
with in that same category before (so a Work meeting doesn't get a
purely-personal friend suggested), and, when the event's location is
geocoded - or, failing that, when its address text names a city you
already have other geocoded locations in - to people whose own usual
hangout region matches where the event actually is (so a Bay Area friend
doesn't get suggested for an LA dinner - and someone you've never
actually shared a located event with anywhere near it simply has no
usual region to match in the first place).

Among whoever qualifies, the picks themselves are random rather than
"most overdue first" - everyone left after the filters above is already a
reasonable suggestion, and always picking the same most-overdue few would
just mean the same handful of names every time; call again (the
dashboard's Refresh button does exactly this) for a different random set.

Deliberately built entirely from your own calendar history (felinni.social,
felinni.regions), unlike the old Future tab's external event search
(felinni.future_events, removed - see git history) which depended on
unreliable third-party search backends. Nothing here makes a network call.
"""
from __future__ import annotations

import random

import pandas as pd

from .regions import guess_region_from_text, location_metro_map
from .social import _exploded_people, person_frequency

MAX_DAYS_SINCE_SEEN = 730  # ~2 years


def _days_since_seen(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Days since you last shared a real, timed event with each person,
    indexed by person."""
    freq = person_frequency(df)
    if freq.empty:
        return pd.Series(dtype=float)
    return (as_of - freq["last_seen"]).dt.total_seconds() / 86400


def _categories_shared_with(df: pd.DataFrame) -> dict[str, set[str]]:
    """Every category you've shared a real, timed event in with each
    person - the "have I ever hung out with them like this" check that
    keeps an upcoming Work meeting from getting a purely-personal friend
    suggested just because they're otherwise overdue."""
    exploded = _exploded_people(df)
    if exploded.empty:
        return {}
    return exploded.groupby("person")["category"].agg(set).to_dict()


def _last_event_with(df: pd.DataFrame) -> dict[str, dict]:
    """Each person's most recent real, timed event with you - title, date,
    and location - the concrete "last time we did something together"
    context shown alongside each upcoming-event suggestion."""
    exploded = _exploded_people(df)
    if exploded.empty:
        return {}
    idx = exploded.groupby("person")["start"].idxmax()
    latest = exploded.loc[idx].set_index("person")
    return {
        person: {
            "title": row["title"],
            "start": row["start"],
            "location": row["location"] if pd.notna(row["location"]) else None,
        }
        for person, row in latest.iterrows()
    }


def _person_usual_regions(df: pd.DataFrame, metro_map: dict[str, str]) -> dict[str, str]:
    """Each person's most common region (metro area, via felinni.regions'
    same location->metro clustering the Travel tab uses) across every
    timed event you've shared with them at a real, identifiable place -
    the place you'd actually invite them to, not just wherever any of your
    events happen to fall. Falls back to `guess_region_from_text` for a
    shared location that hasn't itself been geocoded but names a city you
    have other geocoded locations in - otherwise a person you've only ever
    met at ungeocoded addresses would never get a usual region at all, no
    matter how many of those addresses obviously named the same city. A
    person with no located events at all (e.g. every shared event was a
    Zoom call or otherwise never had a real place attached) simply gets no
    entry here - which is exactly what keeps them out of a region-matched
    upcoming suggestion, with no separate "was this virtual" check needed."""
    located = df.dropna(subset=["location"])
    exploded = _exploded_people(located)
    if exploded.empty:
        return {}
    exploded = exploded.assign(
        region=exploded["location"].map(lambda loc: metro_map.get(loc) or guess_region_from_text(loc, metro_map))
    ).dropna(subset=["region"])
    if exploded.empty:
        return {}
    top_region = exploded.groupby(["person", "region"]).size().groupby(level=0).idxmax()
    return {person: region for person, (_, region) in top_region.items()}


def upcoming_events(df: pd.DataFrame, as_of: pd.Timestamp | None = None, days_ahead: int = 14) -> pd.DataFrame:
    """Timed events already on your calendar in the near future, with a
    real location - all-day placeholders are excluded (same reasoning as
    felinni.social._exploded_people: a full-day block isn't a "hang out"
    the way a timed event is), and so is anything with no location at all,
    since there'd be nothing to invite someone *to* in the geographic
    sense this feature is built around."""
    as_of = as_of or pd.Timestamp.now()
    window_end = as_of + pd.Timedelta(days=days_ahead)
    upcoming = df[~df["is_all_day"] & (df["start"] > as_of) & (df["start"] <= window_end)]
    return upcoming.dropna(subset=["location"]).sort_values("start")


def upcoming_invite_suggestions(
    df: pd.DataFrame,
    geocode_cache: dict | None = None,
    as_of: pd.Timestamp | None = None,
    days_ahead: int = 14,
    top_n: int = 5,
    max_days_since_seen: float = MAX_DAYS_SINCE_SEEN,
    seed: int | None = None,
) -> pd.DataFrame:
    """For each located event already on your calendar in the next
    `days_ahead` days (two weeks by default), who to invite: people you
    haven't shared a real event with recently but still within
    `max_days_since_seen` days (a cap, not a floor - someone from a decade
    ago has likely drifted out of your life for a reason, so they're
    excluded rather than topping the list) who you've also hung out with
    before in that event's own category - so a Work meeting doesn't get a
    purely-personal friend suggested just because they're otherwise
    overdue. When the event's own location is geocoded, also requires the
    person's own usual hangout region (their most common metro area across
    shared, geocoded events) to actually match where the event is - a
    person whose usual region is unknown (most people, unless you've
    geocoded several shared-event locations with them) is excluded too,
    not let through by default, so this stays a real region check rather
    than one only rare conflicts trip - when the event's own location
    isn't geocoded (and its address text doesn't name a known city either),
    there's nothing to check against and this falls back to category
    alone. Both signals are computed from history strictly before `as_of`,
    so an event's own not-yet-real guest list can't skew either, and
    anyone already on the event's guest list is skipped.

    Among whoever qualifies for a given event, `top_n` are picked at
    random (seed it for a reproducible pick, e.g. in a test - the
    dashboard itself calls this unseeded, so a "Refresh" button just
    re-requests and gets a different random set), rather than always the
    most overdue - once someone clears every filter above they're already
    a reasonable suggestion, and ranking by overdue-ness on top of that
    would just mean the same most-overdue handful every time. A person
    already picked for one event is preferred to sit out a later one
    (used to fill it only when nothing else qualifies), so results spread
    across more distinct people instead of the same few repeating.

    Rows come back grouped by event, soonest event first - the order each
    event's rows were built in (upcoming_events is itself start-sorted),
    not re-sorted by any per-row field afterward. Sorting by some
    per-person field across the whole result would interleave rows from
    different events that happen to share an exact start time, breaking
    the per-event grouping the frontend relies on to show each event once.

    Each row carries the title, date, and location of the last real event
    you actually had with that person (`last_event_title`/`_date`/
    `_location`), as concrete context for the suggestion in place of a
    computed explanation."""
    as_of = as_of or pd.Timestamp.now()
    columns = [
        "event_title", "event_category", "event_start", "event_location", "region", "person",
        "days_since_seen", "last_event_title", "last_event_date", "last_event_location",
    ]
    events = upcoming_events(df, as_of=as_of, days_ahead=days_ahead)
    if events.empty:
        return pd.DataFrame(columns=columns)

    rng = random.Random(seed)
    past = df[df["start"] <= as_of]
    days_since_seen = _days_since_seen(past, as_of)
    categories_by_person = _categories_shared_with(past)
    last_event_by_person = _last_event_with(past)

    metro_map = location_metro_map(past, geocode_cache) if geocode_cache else {}
    person_region = _person_usual_regions(past, metro_map) if metro_map else {}

    # Tracks who's already been picked for an earlier (sooner) event, so
    # later events prefer fresh names over re-nominating the same handful
    # of people every time - see the docstring above.
    already_suggested: set[str] = set()

    rows = []
    for _, event in events.iterrows():
        category = event["category"]
        already_invited = set(event["people"]) if isinstance(event["people"], list) else set()
        location = event["location"]
        # This exact address might not itself be geocoded, but its text
        # could still name a city you have other geocoded locations in
        # (e.g. "1903 Hyperion Ave Los Angeles, CA" naming a metro you
        # already know from some other LA venue) - see
        # felinni.regions.guess_region_from_text.
        region = metro_map.get(location) or guess_region_from_text(location, metro_map)

        candidates = [
            person for person, categories in categories_by_person.items()
            if category in categories and person not in already_invited
        ]
        if region:
            # Requires a known, matching region - a person whose usual
            # hangout region we can't determine (most people, unless
            # you've geocoded several shared-event locations with them)
            # is excluded too, not let through by default. Letting unknown
            # region default to "match" would pass nearly everyone, since
            # most contacts won't have enough geocoded shared history to
            # have a known region at all - exactly the "why am I getting
            # people I don't hang out with here" bug this guards against.
            candidates = [p for p in candidates if person_region.get(p) == region]

        eligible = []
        for person in candidates:
            days = days_since_seen.get(person)
            if days is None or pd.isna(days) or days > max_days_since_seen:
                continue
            eligible.append((person, float(days)))
        # Not-yet-suggested people preferred over already-suggested-elsewhere
        # ones (the latter only fill remaining slots), random within each tier.
        fresh = [c for c in eligible if c[0] not in already_suggested]
        reused = [c for c in eligible if c[0] in already_suggested]
        rng.shuffle(fresh)
        rng.shuffle(reused)
        picked = (fresh + reused)[:top_n]
        for person, _ in picked:
            already_suggested.add(person)

        for person, days in picked:
            last_event = last_event_by_person.get(person) or {}
            rows.append({
                "event_title": event["title"],
                "event_category": category,
                "event_start": event["start"],
                "event_location": location,
                "region": region,
                "person": person,
                "days_since_seen": days,
                "last_event_title": last_event.get("title"),
                "last_event_date": last_event.get("start"),
                "last_event_location": last_event.get("location"),
            })

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)
