"""Reconnect suggestions: who's overdue to see again, which of your own
recurring things have gone quiet, who used to show up to a fading
recurring event and might be worth inviting back, and who to invite to
events already sitting on your calendar in the near future - filtered to
people whose own usual hangout region matches where that event actually
is, so a Bay Area friend doesn't get suggested for an LA dinner.

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


def people_to_reconnect_with(df: pd.DataFrame, min_events: int = 2, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Ranks people by how overdue they are for a get-together, relative to
    how often you *used* to see them - not just by raw days since last
    seen, which would just rank your least-frequent contacts as "most
    overdue" forever. `overdue_ratio` is days-since-last-seen divided by
    their own average gap between events, so someone you saw every few
    days and haven't seen in a month reads as more overdue than someone
    you've only ever seen once a year and saw last month.

    People with fewer than `min_events` events don't have enough history
    to infer a usual gap from, and are dropped (a single shared event has
    no "interval" at all)."""
    as_of = as_of or pd.Timestamp.now()
    freq = person_frequency(df)
    freq = freq[freq["events"] >= min_events].copy()
    if freq.empty:
        return freq.assign(avg_interval_days=[], days_since_seen=[], overdue_ratio=[], recent_events=[])

    span_days = (freq["last_seen"] - freq["first_seen"]).dt.total_seconds() / 86400
    # events-1 gaps span the history; a lone repeat (events==2) still gets
    # one real gap rather than dividing by zero.
    freq["avg_interval_days"] = span_days / (freq["events"] - 1).clip(lower=1)
    freq["days_since_seen"] = (as_of - freq["last_seen"]).dt.total_seconds() / 86400
    freq["overdue_ratio"] = freq["days_since_seen"] / freq["avg_interval_days"].replace(0, pd.NA)

    exploded = _exploded_people(df)
    recent_titles = (
        exploded.sort_values("start")
        .groupby("person")["title"]
        .agg(lambda titles: list(dict.fromkeys(titles.tail(5).tolist()[::-1]))[:3])
    )
    freq["recent_events"] = freq.index.map(recent_titles).map(lambda v: v if isinstance(v, list) else [])

    return freq.reset_index().sort_values("overdue_ratio", ascending=False).reset_index(drop=True)


def events_to_revive(df: pd.DataFrame, as_of: pd.Timestamp | None = None, min_occurrences: int = 4) -> pd.DataFrame:
    """Your own recurring things (felinni.recurring) that have gone quiet -
    "slowing down" or fully "stopped" relative to their usual cadence -
    the events worth putting back on the calendar."""
    series = recurring_series(df, min_occurrences=min_occurrences, as_of=as_of)
    if series.empty:
        return series
    return series[series["status"].isin(["slowing down", "stopped"])].reset_index(drop=True)


def suggested_invites(
    df: pd.DataFrame, as_of: pd.Timestamp | None = None, min_occurrences: int = 4, top_attendees: int = 4,
) -> pd.DataFrame:
    """One row per (fading recurring event, past attendee) pairing: for
    each event you've let slide, who used to come to it and how overdue
    are *they* individually - so "revive Book Club" comes with a ready
    list of who to actually text about it, prioritized by their own
    overdue_ratio rather than just how often they've attended in total."""
    as_of = as_of or pd.Timestamp.now()
    series = events_to_revive(df, as_of=as_of, min_occurrences=min_occurrences)
    columns = [
        "series_title", "series_category", "series_status", "series_cadence", "series_days_since_last",
        "person", "times_attended", "days_since_seen", "overdue_ratio",
    ]
    if series.empty:
        return pd.DataFrame(columns=columns)

    people_stats = people_to_reconnect_with(df, min_events=1, as_of=as_of).set_index("person")
    normalized = df.assign(_series_title=df["title"].apply(strip_with_suffix))

    rows = []
    for _, s in series.iterrows():
        series_events = normalized[normalized["_series_title"] == s["title"]]
        exploded = _exploded_people(series_events)
        if exploded.empty:
            continue
        for person, count in exploded["person"].value_counts().head(top_attendees).items():
            stat = people_stats.loc[person] if person in people_stats.index else None
            rows.append({
                "series_title": s["title"],
                "series_category": s["category"],
                "series_status": s["status"],
                "series_cadence": s["cadence"],
                "series_days_since_last": s["days_since_last"],
                "person": person,
                "times_attended": int(count),
                "days_since_seen": float(stat["days_since_seen"]) if stat is not None else None,
                "overdue_ratio": float(stat["overdue_ratio"]) if stat is not None and pd.notna(stat["overdue_ratio"]) else None,
            })

    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(rows, columns=columns)
    return result.sort_values(
        ["series_days_since_last", "overdue_ratio"], ascending=[False, False], na_position="last",
    ).reset_index(drop=True)


def upcoming_events(df: pd.DataFrame, as_of: pd.Timestamp | None = None, days_ahead: int = 60) -> pd.DataFrame:
    """Timed events already on your calendar in the near future (all-day
    placeholders excluded, same reasoning as felinni.social._exploded_people
    - a full-day block isn't a "hang out" the way a timed event is)."""
    as_of = as_of or pd.Timestamp.now()
    window_end = as_of + pd.Timedelta(days=days_ahead)
    upcoming = df[~df["is_all_day"] & (df["start"] > as_of) & (df["start"] <= window_end)]
    return upcoming.sort_values("start")


def _person_usual_regions(df: pd.DataFrame, metro_map: dict[str, str]) -> dict[str, str]:
    """Each person's most common region (metro area, via felinni.regions'
    same location->metro clustering the Travel tab uses) across every
    timed, geocoded event you've shared with them - the place you'd
    actually invite them to, not just wherever any of your events happen
    to fall. Ties/majority broken by number of distinct shared events in
    that region, same as a person's overall event count elsewhere."""
    located = df.dropna(subset=["location"])
    exploded = _exploded_people(located)
    if exploded.empty:
        return {}
    exploded = exploded.assign(region=exploded["location"].map(metro_map.get)).dropna(subset=["region"])
    if exploded.empty:
        return {}
    top_region = exploded.groupby(["person", "region"]).size().groupby(level=0).idxmax()
    return {person: region for person, (_, region) in top_region.items()}


def upcoming_invite_suggestions(
    df: pd.DataFrame,
    geocode_cache: dict,
    as_of: pd.Timestamp | None = None,
    days_ahead: int = 60,
    top_n: int = 5,
    min_overdue_ratio: float = 1.0,
) -> pd.DataFrame:
    """For each upcoming, geocoded event already on your calendar, who to
    invite: people who are overdue to see (`people_to_reconnect_with`)
    *and* whose own usual hangout region matches where the event actually
    is (`_person_usual_regions`) - so an overdue Bay Area friend doesn't
    get suggested for an LA dinner just because they're overdue in the
    abstract. Both the "overdue" and "usual region" signals are computed
    from history strictly before `as_of`, so an event's own not-yet-real
    guest list can't skew either. Needs geocoded locations (the Map tab's
    "Geocode locations" button) - with nothing geocoded yet, there's no
    region to match against and this returns empty."""
    as_of = as_of or pd.Timestamp.now()
    columns = [
        "event_title", "event_start", "event_location", "region",
        "person", "days_since_seen", "overdue_ratio",
    ]
    if not geocode_cache:
        return pd.DataFrame(columns=columns)

    events = upcoming_events(df, as_of=as_of, days_ahead=days_ahead).dropna(subset=["location"])
    if events.empty:
        return pd.DataFrame(columns=columns)

    past = df[df["start"] <= as_of]
    metro_map = location_metro_map(past, geocode_cache)
    if not metro_map:
        return pd.DataFrame(columns=columns)

    person_region = _person_usual_regions(past, metro_map)
    people_stats = people_to_reconnect_with(past, min_events=1, as_of=as_of).set_index("person")

    rows = []
    for _, event in events.iterrows():
        region = metro_map.get(event["location"])
        if not region:
            continue
        already_invited = set(event["people"]) if isinstance(event["people"], list) else set()
        candidates = [p for p, r in person_region.items() if r == region and p not in already_invited]

        scored = []
        for person in candidates:
            if person not in people_stats.index:
                continue
            ratio = people_stats.loc[person, "overdue_ratio"]
            if pd.isna(ratio) or ratio < min_overdue_ratio:
                continue
            scored.append((person, float(ratio), float(people_stats.loc[person, "days_since_seen"])))
        scored.sort(key=lambda t: t[1], reverse=True)

        for person, ratio, days_since in scored[:top_n]:
            rows.append({
                "event_title": event["title"],
                "event_start": event["start"],
                "event_location": event["location"],
                "region": region,
                "person": person,
                "days_since_seen": days_since,
                "overdue_ratio": ratio,
            })

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["event_start", "overdue_ratio"], ascending=[True, False],
    ).reset_index(drop=True)
