"""Tests for felinni.recommendations: "overdue" is relative to each
person's (and activity's) own rhythm, already-scheduled plans suppress a
suggestion, Work is skipped by default, and event ideas invite the most
overdue regulars."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import ingest, recommendations

AS_OF = pd.Timestamp("2024-06-01")


def _event(idx, title, start, people, category="Social", hours=2):
    start = pd.Timestamp(start)
    return {
        "id": f"evt-{idx}",
        "title": title,
        "notes": None,
        "location": None,
        "startDate": start.isoformat() + "Z",
        "endDate": (start + pd.Timedelta(hours=hours)).isoformat() + "Z",
        "isAllDay": False,
        "calendarTitle": category,
        "calendarColorHex": None,
        "attendees": people,
        "isRecurring": False,
        "url": None,
        "noteTags": {},
    }


def _every(start, days, n):
    return [pd.Timestamp(start) + pd.Timedelta(days=days * i) for i in range(n)]


def _df(events):
    return ingest.load_events_from_records(events)


def _build(extra=()):
    events, idx = [], 0
    # Alice: weekly drinks, last one 2024-03-25 -> ~10 weeks overdue.
    for t in _every("2024-01-01 19:00", 7, 13):
        events.append(_event(idx := idx + 1, "Drinks", t, ["Alice"]))
    # Bob: dinner every ~3 months, last 2024-04-01 -> 2 months ago, on track.
    for t in _every("2023-04-01 19:00", 91, 5):
        events.append(_event(idx := idx + 1, "Dinner", t, ["Bob"]))
    # Game night with Carla + Diego every 2 weeks, stopped in January.
    for t in _every("2023-09-01 19:00", 14, 10):
        events.append(_event(idx := idx + 1, "Game night", t, ["Carla", "Diego"]))
    # Coworker at a weekly Work meeting, stopped in March.
    for t in _every("2024-01-02 09:00", 7, 10):
        events.append(_event(idx := idx + 1, "Sync", t, ["Pat"], category="Work"))
    events.extend(extra)
    return _df(events)


def test_activity_name_strips_the_with_clause():
    assert recommendations.activity_name("Dinner with Bob", "Social") == "Dinner"
    assert recommendations.activity_name("Game night with Alice, Bob, and Carla", "Social") == "Game night"
    assert recommendations.activity_name("with Alice", "Social") == "Social"


def test_overdue_is_relative_to_each_persons_usual_rhythm():
    people = {r["person"]: r for r in recommendations.people_to_reconnect(_build(), as_of=AS_OF)}
    assert people["Alice"]["status"] == "overdue"
    assert people["Alice"]["typical_gap_days"] == 7
    assert people["Alice"]["usual_activity"] == "Drinks"
    # Bob's ~2 months since dinner is normal for a quarterly friend.
    assert "Bob" not in people
    # Carla and Diego always come together.
    assert people["Carla"]["usually_with"] == ["Diego"]


def test_work_is_skipped_by_default_but_can_be_included():
    df = _build()
    assert "Pat" not in {r["person"] for r in recommendations.people_to_reconnect(df, as_of=AS_OF)}
    included = recommendations.people_to_reconnect(df, as_of=AS_OF, skip_categories=())
    assert "Pat" in {r["person"] for r in included}


def test_someone_already_scheduled_is_not_suggested():
    df = _build(extra=[_event(999, "Drinks", "2024-06-05 19:00", ["Alice"])])
    people = {r["person"] for r in recommendations.people_to_reconnect(df, as_of=AS_OF)}
    assert "Alice" not in people
    ideas = {i["activity"] for i in recommendations.event_ideas(df, as_of=AS_OF)}
    assert "Drinks" not in ideas


def test_people_ranked_by_how_overdue_and_how_regular():
    rows = recommendations.people_to_reconnect(_build(), as_of=AS_OF)
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)


def test_event_ideas_invite_the_usual_group_size():
    ideas = {i["activity"]: i for i in recommendations.event_ideas(_build(), as_of=AS_OF)}
    assert set(ideas["Game night"]["invite"]) == {"Carla", "Diego"}
    assert ideas["Drinks"]["invite"] == ["Alice"]
    assert "Dinner" not in ideas  # neither the activity nor Bob is overdue


def test_one_on_one_idea_invites_the_most_overdue_regular():
    events = []
    # Dinner with Emi most recently (on track), Frank long ago (overdue).
    for i, t in enumerate(_every("2023-01-01 19:00", 30, 6)):
        events.append(_event(100 + i, "Dinner", t, ["Frank"]))
    for i, t in enumerate(_every("2024-01-01 19:00", 30, 6)):
        events.append(_event(200 + i, "Dinner", t, ["Emi"]))
    ideas = recommendations.event_ideas(_df(events), as_of=pd.Timestamp("2024-06-10"))
    dinner = next(i for i in ideas if i["activity"] == "Dinner")
    assert dinner["invite"] == ["Frank"]
    assert dinner["also_invited_before"] == ["Emi"]


def test_empty_dataset_gives_no_recommendations():
    df = _df([])
    assert recommendations.people_to_reconnect(df, as_of=AS_OF) == []
    assert recommendations.event_ideas(df, as_of=AS_OF) == []


# --- week_plan ---
# AS_OF is Saturday 2024-06-01, so the planned week is Sun 06-02 .. Sat 06-08.
WEEK = [pd.Timestamp("2024-06-02") + pd.Timedelta(days=i) for i in range(7)]


def _plan(df, **kwargs):
    return recommendations.week_plan(df, as_of=AS_OF, zone="UTC", **kwargs)


def test_week_plan_covers_the_next_seven_days():
    week = _plan(_build())
    assert week["week_start"] == WEEK[0]
    assert week["week_end"] == WEEK[-1]
    for plan in week["plans"]:
        assert WEEK[0] <= plan["start"] < WEEK[-1] + pd.Timedelta(days=1)


def test_week_plan_uses_the_usual_weekday_and_time():
    # Game nights were every other Friday at 7pm (2023-09-01 was a Friday).
    plans = {p["activity"]: p for p in _plan(_build())["plans"]}
    game = plans["Game night"]
    assert game["start"] == pd.Timestamp("2024-06-07 19:00")
    assert game["end"] == pd.Timestamp("2024-06-07 21:00")
    assert set(game["invite"]) == {"Carla", "Diego"}


def test_week_plan_avoids_whatever_is_already_scheduled():
    # Something else already on that Friday evening.
    busy = _event(900, "Concert", "2024-06-07 18:00", [], category="Personal", hours=4)
    game = next(p for p in _plan(_build(extra=[busy]))["plans"] if p["activity"] == "Game night")
    assert game["start"].date() != pd.Timestamp("2024-06-07").date()
    assert game["start"].hour == 19


def test_week_plan_includes_people_who_tip_into_due_during_the_week():
    events = [_event(i, "Coffee", t, ["Gus"]) for i, t in enumerate(_every("2024-01-06 10:00", 30, 5))]
    # Last coffee 2024-05-05: 27 days ago on AS_OF (not due yet), 34 by week's end.
    df = _df(events)
    assert recommendations.people_to_reconnect(df, as_of=AS_OF) == []
    plans = _plan(df)["plans"]
    assert [p["invite"] for p in plans] == [["Gus"]]


def test_week_plan_treats_something_scheduled_this_week_as_done():
    df = _build(extra=[_event(901, "Game night", "2024-06-04 19:00", ["Carla", "Diego"])])
    assert "Game night" not in {p["activity"] for p in _plan(df)["plans"]}


def test_week_plan_never_gives_anyone_two_one_on_ones():
    events = []
    for i, t in enumerate(_every("2024-01-01 19:00", 7, 10)):
        events.append(_event(300 + i, "Dinner", t, ["Hana"]))
    for i, t in enumerate(_every("2024-01-03 20:00", 7, 10)):
        events.append(_event(400 + i, "Drinks", t, ["Hana"]))
    plans = _plan(_df(events))["plans"]
    assert sum("Hana" in p["invite"] for p in plans) == 1


def test_week_plan_respects_max_plans():
    assert len(_plan(_build(), max_plans=1)["plans"]) == 1
