"""Recommendations for the Future tab, built purely from your own history:
who you're overdue to see (judged against how often you *usually* see
them, not a fixed threshold), and concrete things to plan - an activity
you've done with people before, plus who to invite to it.

"Overdue" is always relative to each person's (or activity's) own rhythm:
someone you see weekly is overdue after a few weeks, someone you see
twice a year isn't overdue until well past six months. Events scheduled
after `as_of` count as "already planned" rather than history, so someone
you've already got something on the calendar with isn't nagged about.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import pandas as pd
from dateutil import tz as dateutil_tz

from felinni import social
from felinni.ingest import _WITH_PATTERN

# Caps how much being overdue can count toward a score, so one
# acquaintance you haven't seen in five years doesn't permanently outrank
# a close friend who's merely a couple of months late.
MAX_OVERDUE_RATIO = 6.0

# Categories left out by default: coworkers you see at a daily standup
# aren't people you need reminding to make plans with, and a meeting
# isn't something you'd invite friends to.
DEFAULT_SKIP_CATEGORIES = ("Work",)

_WHITESPACE = re.compile(r"\s+")


def _now() -> pd.Timestamp:
    # felinni.ingest stores naive UTC timestamps.
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def activity_name(title: str | None, category: str | None) -> str:
    """The "what" of an event with its "with <names>" suffix dropped -
    "Dinner with Carla" and "Dinner with Bob" are both "Dinner". Falls back
    to the category when the title is nothing but a name list (or empty)."""
    name = (title or "").strip()
    match = _WITH_PATTERN.search(name)
    if match:
        name = name[:match.start()]
    name = _WHITESPACE.sub(" ", name).strip(" -:,")
    return name or (category or "Hang out")


def _status(ratio: float) -> str:
    if ratio >= 2:
        return "overdue"
    if ratio >= 1:
        return "due"
    return "on track"


def _split(
    df: pd.DataFrame, as_of: pd.Timestamp, skip_categories,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-person rows (all-day events excluded, same as felinni.social),
    minus `skip_categories`, split into history (before `as_of`) and
    already-planned (after)."""
    skipped = {c.casefold() for c in skip_categories}
    if skipped:
        df = df[~df["category"].fillna("").str.casefold().isin(skipped)]
    exploded = social._exploded_people(df)
    if exploded.empty:
        return exploded, exploded
    exploded = exploded.assign(activity=[
        activity_name(t, c) for t, c in zip(exploded["title"], exploded["category"])
    ])
    return exploded[exploded["start"] < as_of], exploded[exploded["start"] >= as_of]


def _typical_gap_days(starts: pd.Series) -> float | None:
    """Median days between distinct days something happened - distinct
    days so two events with someone on the same day don't read as a 0-day
    gap. None with fewer than two distinct days."""
    days = starts.dt.normalize().drop_duplicates().sort_values()
    if len(days) < 2:
        return None
    return float(days.diff().dropna().dt.days.median())


def people_to_reconnect(
    df: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    min_events: int = 3,
    limit: int | None = 15,
    skip_categories=DEFAULT_SKIP_CATEGORIES,
) -> list[dict]:
    """People you're due or overdue to see, most pressing first.

    Only people seen on at least `min_events` distinct days count - with
    fewer there's no real rhythm to be late against. A person's score is
    how overdue they are (days since you last saw them / their usual gap,
    capped at MAX_OVERDUE_RATIO) weighted by how much of a regular they
    are (log of event count), so a long-standing friend who's slipping
    ranks above someone you met a few times years ago. Anyone you already
    have something scheduled with after `as_of` is left out, as is every
    event in `skip_categories`."""
    as_of = as_of or _now()
    past, upcoming = _split(df, as_of, skip_categories)
    if past.empty:
        return []
    planned = set(upcoming["person"])
    people_by_event = past.groupby("id")["person"].apply(list).to_dict()

    rows = []
    for person, events in past.groupby("person"):
        if person in planned or events["start"].dt.normalize().nunique() < min_events:
            continue
        gap = _typical_gap_days(events["start"])
        if not gap:
            continue
        last_seen = events["start"].max()
        days_since = (as_of - last_seen).days
        ratio = days_since / gap
        if ratio < 1:
            continue

        # Who else tends to be there, and what you usually do together.
        others = Counter(
            other for event_id in events["id"] for other in people_by_event[event_id] if other != person
        )
        usual_activity = events["activity"].value_counts().idxmax()
        rows.append({
            "person": person,
            "events": int(len(events)),
            "last_seen": last_seen,
            "last_activity": events.loc[events["start"].idxmax(), "activity"],
            "days_since": int(days_since),
            "typical_gap_days": gap,
            "overdue_ratio": ratio,
            "status": _status(ratio),
            "usual_activity": usual_activity,
            "usually_with": [p for p, _ in others.most_common(3)],
            "score": min(ratio, MAX_OVERDUE_RATIO) * math.log1p(len(events)),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit] if limit else rows


def event_ideas(
    df: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    min_occurrences: int = 3,
    limit: int | None = 8,
    skip_categories=DEFAULT_SKIP_CATEGORIES,
) -> list[dict]:
    """Things worth putting on the calendar: activities you've done with
    people at least `min_occurrences` times, each with who to invite.

    Invitees are the activity's regulars (anyone there at least twice),
    most-overdue-to-see first - so a one-on-one "Dinner" you've had with
    several people suggests whoever you've gone longest without seeing,
    and a group "Game night" suggests a group of its usual size. An idea
    ranks higher the more overdue both the activity itself and its
    invitees are. Activities with something already scheduled after
    `as_of` are skipped."""
    as_of = as_of or _now()
    past, upcoming = _split(df, as_of, skip_categories)
    if past.empty:
        return []
    planned_activities = {a.casefold() for a in upcoming["activity"]}
    people_ratio = {
        r["person"]: r["overdue_ratio"]
        for r in people_to_reconnect(df, as_of=as_of, min_events=2, limit=None, skip_categories=skip_categories)
    }

    ideas = []
    for key, rows in past.groupby(past["activity"].str.casefold()):
        if key in planned_activities:
            continue
        occurrences = rows.drop_duplicates("id")
        if len(occurrences) < min_occurrences:
            continue

        attendance = rows["person"].value_counts()
        regulars = [p for p, n in attendance.items() if n >= 2]
        if not regulars:
            continue
        # Most overdue first; ties (including anyone not overdue at all)
        # broken by how often they've come.
        regulars.sort(key=lambda p: (-people_ratio.get(p, 0.0), -attendance[p]))
        group_size = max(1, round(rows.groupby("id").size().median()))
        invite = regulars[:group_size]

        gap = _typical_gap_days(occurrences["start"])
        last_done = occurrences["start"].max()
        days_since = (as_of - last_done).days
        activity_ratio = days_since / gap if gap else 0.0
        invitee_ratio = sum(min(people_ratio.get(p, 0.0), MAX_OVERDUE_RATIO) for p in invite) / len(invite)
        if activity_ratio < 1 and invitee_ratio < 1:
            continue

        ideas.append({
            "activity": occurrences["activity"].value_counts().idxmax(),
            "category": occurrences["category"].value_counts().idxmax(),
            "invite": invite,
            "also_invited_before": [p for p in regulars if p not in invite][:4],
            "times_done": int(len(occurrences)),
            "last_done": last_done,
            "days_since": int(days_since),
            "typical_gap_days": gap,
            "overdue_ratio": activity_ratio,
            "score": min(activity_ratio, MAX_OVERDUE_RATIO) + invitee_ratio,
        })
    ideas.sort(key=lambda r: r["score"], reverse=True)
    return ideas[:limit] if limit else ideas


def _to_local(starts: pd.Series, zone) -> pd.Series:
    """Naive-UTC timestamps (felinni.ingest's storage) -> naive local wall
    clock in `zone`, so "usually Friday at 7pm" means your Friday 7pm."""
    return starts.dt.tz_localize("UTC").dt.tz_convert(zone).dt.tz_localize(None)


def _to_utc(local: pd.Timestamp, zone) -> pd.Timestamp:
    return local.tz_localize(zone).tz_convert("UTC").tz_localize(None)


def _slot(history: pd.DataFrame, window_days: list[pd.Timestamp], busy: list[tuple], zone):
    """The first free slot in `window_days` that fits how this has
    usually gone: days ordered by how often it's happened on that weekday
    (then earliest first), at its most common local start time and median
    length. A day is skipped if that slot overlaps anything in `busy`.
    None if every day in the window clashes."""
    local = _to_local(history["start"], zone)
    minutes = (local.dt.hour * 60 + (local.dt.minute // 30) * 30).value_counts().idxmax()
    length = pd.Timedelta(hours=float(history["duration_hours"].median()))
    weekday_counts = local.dt.dayofweek.value_counts()
    for day in sorted(window_days, key=lambda d: -weekday_counts.get(d.dayofweek, 0)):
        start = day + pd.Timedelta(minutes=int(minutes))
        end = start + length
        if not any(b_start < end and start < b_end for b_start, b_end in busy):
            return start, end
    return None


def week_plan(
    df: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    days: int = 7,
    max_plans: int = 5,
    skip_categories=DEFAULT_SKIP_CATEGORIES,
    busy_df: pd.DataFrame | None = None,
    zone=None,
) -> dict:
    """A ready-made plan for the `days` days after `as_of`: which event
    ideas and catch-ups to schedule, who to invite, and a suggested day
    and time for each.

    What's due is judged as of the *end* of that window, so someone who'll
    tip into overdue by next Sunday is included, and anything already on
    your calendar within the window counts as done. Event ideas come
    first (best-scoring first), then one-on-one catch-ups for anyone still
    due who isn't already invited to something - nobody is invited twice
    in the same week. Each lands on the first free day that matches when
    that activity (or time with that person) usually happens, at its usual
    local start time, avoiding anything already scheduled in `busy_df`
    (defaults to `df`; pass the unfiltered dataset so e.g. hidden Work
    meetings still block time) and the other plans. Anything that can't
    find a free slot is dropped rather than double-booked."""
    zone = zone or dateutil_tz.tzlocal()
    as_of = as_of or _now()
    local_today = _to_local(pd.Series([as_of]), zone).iloc[0].normalize()
    window_days = [local_today + pd.Timedelta(days=i) for i in range(1, days + 1)]
    window_end = _to_utc(window_days[-1] + pd.Timedelta(days=1), zone)

    busy_source = df if busy_df is None else busy_df
    timed = busy_source[~busy_source["is_all_day"]]
    busy = list(zip(_to_local(timed["start"], zone), _to_local(timed["end"], zone)))

    past, _ = _split(df, window_end, skip_categories)
    due = {
        p["person"]: p
        for p in people_to_reconnect(df, as_of=window_end, limit=None, skip_categories=skip_categories)
    }
    plans, invited = [], set()

    def ago(ts):
        return f"{(as_of - ts).days} days ago"

    def add(kind, activity, invite, history, reason):
        slot = _slot(history, window_days, busy, zone)
        if slot is None:
            return
        busy.append(slot)
        invited.update(invite)
        plans.append({
            "kind": kind,
            "activity": activity,
            "invite": invite,
            "start": slot[0],
            "end": slot[1],
            "reason": reason,
        })

    ideas = event_ideas(df, as_of=window_end, limit=None, skip_categories=skip_categories)
    # Group ideas first (still best-scoring first within each), so a
    # one-on-one doesn't claim someone who's already coming to game night.
    ideas.sort(key=lambda idea: len(idea["invite"]) == 1)
    for idea in ideas:
        if len(plans) >= max_plans:
            break
        invite = idea["invite"]
        if len(invite) == 1:
            # One-on-one: nobody gets two of these in one week - fall back
            # to the next regular who isn't already invited to something.
            candidates = [p for p in invite + idea["also_invited_before"] if p not in invited]
            if not candidates:
                continue
            invite = candidates[:1]
        history = past[past["activity"].str.casefold() == idea["activity"].casefold()].drop_duplicates("id")
        reason = (
            f"Done {idea['times_done']}x, usually every {round(idea['typical_gap_days'])} days"
            f" - last time {ago(idea['last_done'])}."
        )
        due_invitees = [p for p in invite if p in due]
        if due_invitees:
            reason += f" Due to see {', '.join(due_invitees)}."
        add("idea", idea["activity"], invite, history, reason)

    for person in due.values():
        if len(plans) >= max_plans:
            break
        if person["person"] in invited:
            continue
        add("catch-up", person["usual_activity"], [person["person"]], past[past["person"] == person["person"]], (
            f"You usually see {person['person']} every {round(person['typical_gap_days'])} days"
            f" - last time {ago(person['last_seen'])}."
        ))

    plans.sort(key=lambda p: p["start"])
    return {"week_start": window_days[0], "week_end": window_days[-1], "plans": plans}
