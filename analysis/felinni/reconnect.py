"""Reconnect suggestions: which of your own recurring things have gone
quiet and who to invite back to each one, and who to invite to events
already sitting on your calendar in the next two weeks.

Both are capped at people last seen within MAX_DAYS_SINCE_SEEN (a flat
threshold rather than a ratio against their own usual cadence - a ratio
makes a rare, one-off contact from years ago look permanently "overdue"
off a single data point). This is a ceiling, not a floor: someone you
haven't shared an event with in a decade has likely drifted out of your
life for a reason and isn't a useful "reconnect" suggestion, so they're
excluded entirely rather than topping the list just for being the most
overdue. Upcoming-event suggestions are further narrowed to people you've
actually hung out with in that same category before (so a Work meeting
doesn't get a purely-personal friend suggested) and, when the event's
location is geocoded, to people whose own usual hangout region matches
where the event actually is (so a Bay Area friend doesn't get suggested
for an LA dinner).

Deliberately built entirely from your own calendar history (felinni.social,
felinni.recurring, felinni.regions), unlike the old Future tab's external
event search (felinni.future_events, removed - see git history) which
depended on unreliable third-party search backends. Nothing here makes a
network call."""
from __future__ import annotations

import pandas as pd

from .ingest import strip_with_suffix
from .recurring import recurring_series
from .regions import location_metro_map
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


def _person_usual_regions(df: pd.DataFrame, metro_map: dict[str, str]) -> dict[str, str]:
    """Each person's most common region (metro area, via felinni.regions'
    same location->metro clustering the Travel tab uses) across every
    timed, geocoded event you've shared with them - the place you'd
    actually invite them to, not just wherever any of your events happen
    to fall."""
    located = df.dropna(subset=["location"])
    exploded = _exploded_people(located)
    if exploded.empty:
        return {}
    exploded = exploded.assign(region=exploded["location"].map(metro_map.get)).dropna(subset=["region"])
    if exploded.empty:
        return {}
    top_region = exploded.groupby(["person", "region"]).size().groupby(level=0).idxmax()
    return {person: region for person, (_, region) in top_region.items()}


def events_to_revive(df: pd.DataFrame, as_of: pd.Timestamp | None = None, min_occurrences: int = 4) -> pd.DataFrame:
    """Your own recurring things (felinni.recurring) that have gone quiet -
    "slowing down" or fully "stopped" relative to their usual cadence -
    the events worth putting back on the calendar."""
    series = recurring_series(df, min_occurrences=min_occurrences, as_of=as_of)
    if series.empty:
        return series
    return series[series["status"].isin(["slowing down", "stopped"])].reset_index(drop=True)


def suggested_invites(
    df: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    min_occurrences: int = 4,
    top_attendees: int = 4,
    max_days_since_seen: float = MAX_DAYS_SINCE_SEEN,
) -> pd.DataFrame:
    """One row per (fading recurring event, past regular) pairing, capped
    to regulars last seen within `max_days_since_seen` days - so reviving
    "Book Club" surfaces people you've actually drifted from recently, not
    someone from a decade ago you likely haven't kept up with for a
    reason - most overdue (within that cap) first."""
    as_of = as_of or pd.Timestamp.now()
    series = events_to_revive(df, as_of=as_of, min_occurrences=min_occurrences)
    columns = [
        "series_title", "series_category", "series_status", "series_cadence", "series_days_since_last",
        "person", "times_attended", "days_since_seen",
    ]
    if series.empty:
        return pd.DataFrame(columns=columns)

    days_since_seen = _days_since_seen(df, as_of)
    normalized = df.assign(_series_title=df["title"].apply(strip_with_suffix))

    rows = []
    for _, s in series.iterrows():
        series_events = normalized[normalized["_series_title"] == s["title"]]
        exploded = _exploded_people(series_events)
        if exploded.empty:
            continue
        for person, count in exploded["person"].value_counts().head(top_attendees).items():
            days = days_since_seen.get(person)
            if days is None or pd.isna(days) or days > max_days_since_seen:
                continue
            rows.append({
                "series_title": s["title"],
                "series_category": s["category"],
                "series_status": s["status"],
                "series_cadence": s["cadence"],
                "series_days_since_last": s["days_since_last"],
                "person": person,
                "times_attended": int(count),
                "days_since_seen": float(days),
            })

    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(rows, columns=columns)
    return result.sort_values(
        ["series_days_since_last", "days_since_seen"], ascending=[False, False],
    ).reset_index(drop=True)


def upcoming_events(df: pd.DataFrame, as_of: pd.Timestamp | None = None, days_ahead: int = 14) -> pd.DataFrame:
    """Timed events already on your calendar in the near future (all-day
    placeholders excluded, same reasoning as felinni.social._exploded_people
    - a full-day block isn't a "hang out" the way a timed event is)."""
    as_of = as_of or pd.Timestamp.now()
    window_end = as_of + pd.Timedelta(days=days_ahead)
    upcoming = df[~df["is_all_day"] & (df["start"] > as_of) & (df["start"] <= window_end)]
    return upcoming.sort_values("start")


def upcoming_invite_suggestions(
    df: pd.DataFrame,
    geocode_cache: dict | None = None,
    as_of: pd.Timestamp | None = None,
    days_ahead: int = 14,
    top_n: int = 5,
    max_days_since_seen: float = MAX_DAYS_SINCE_SEEN,
) -> pd.DataFrame:
    """For each event already on your calendar in the next `days_ahead`
    days (two weeks by default), who to invite: people you haven't shared
    a real event with recently but still within `max_days_since_seen` days
    (a cap, not a floor - someone from a decade ago has likely drifted out
    of your life for a reason, so they're excluded rather than topping the
    list) who you've also hung out with before in that event's own
    category - so a Work meeting doesn't get a purely-personal friend
    suggested just because they're otherwise overdue. When the event's own
    location is geocoded, also requires the person's own usual hangout
    region (their most common metro area across shared, geocoded events)
    to actually match where the event is - a person whose usual region is
    unknown (most people, unless you've geocoded several shared-event
    locations with them) is excluded too, not let through by default, so
    this stays a real region check rather than one only rare conflicts
    trip. Both signals are computed from history strictly before `as_of`,
    so an event's own not-yet-real guest list can't skew either, and
    anyone already on the event's guest list is skipped. Each row carries
    a plain-English `reason` for why that person was picked."""
    as_of = as_of or pd.Timestamp.now()
    columns = [
        "event_title", "event_category", "event_start", "event_location", "region",
        "person", "days_since_seen", "reason",
    ]
    events = upcoming_events(df, as_of=as_of, days_ahead=days_ahead)
    if events.empty:
        return pd.DataFrame(columns=columns)

    past = df[df["start"] <= as_of]
    days_since_seen = _days_since_seen(past, as_of)
    categories_by_person = _categories_shared_with(past)

    metro_map = location_metro_map(past, geocode_cache) if geocode_cache else {}
    person_region = _person_usual_regions(past, metro_map) if metro_map else {}

    rows = []
    for _, event in events.iterrows():
        category = event["category"]
        already_invited = set(event["people"]) if isinstance(event["people"], list) else set()
        location = event["location"]
        region = metro_map.get(location) if pd.notna(location) else None

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

        scored = []
        for person in candidates:
            days = days_since_seen.get(person)
            if days is None or pd.isna(days) or days > max_days_since_seen:
                continue
            scored.append((person, float(days)))
        scored.sort(key=lambda t: t[1], reverse=True)

        for person, days in scored[:top_n]:
            reason = f"You've been to {category} events with them before, last {round(days)} days ago"
            if region and person_region.get(person) == region:
                reason += f", usually around {region}"
            rows.append({
                "event_title": event["title"],
                "event_category": category,
                "event_start": event["start"],
                "event_location": location,
                "region": region,
                "person": person,
                "days_since_seen": days,
                "reason": reason,
            })

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["event_start", "days_since_seen"], ascending=[True, False],
    ).reset_index(drop=True)
