"""Tests for felinni.future_events: parsing/filtering DuckDuckGo HTML search
results and each event page's schema.org JSON-LD into the Future tab's event
shape, conflict detection against the calendar and other candidates, and
ranking by fit with calendar history. Network calls are always mocked via
requests.post/requests.get (see felinni.calendar_sources's tests for the
same pattern) - these never hit a real endpoint."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import future_events, ingest

# A trimmed but structurally faithful DuckDuckGo HTML results page: one
# meetup.com hit (should survive the domain filter), one unrelated hit
# (should be dropped even though it matched the query), and a result whose
# link is wrapped in DDG's outbound-redirect format (should be unwrapped).
SAMPLE_DDG_HTML = """
<div class="result results_links results_links_deep web-result">
  <div class="result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="https://www.meetup.com/la-hikers/events/123/">LA Hikers &amp; Outdoors <b>Meetup</b></a>
    </h2>
    <a class="result__snippet" href="https://www.meetup.com/la-hikers/events/123/">Join us for a sunrise hike in Griffith Park this Saturday.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="https://www.some-blog.com/best-la-events">Best LA Events This Month</a>
    </h2>
    <a class="result__snippet" href="https://www.some-blog.com/best-la-events">A roundup of things to do around town.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.meetup.com%2Fla-coders%2Fevents%2F456%2F&amp;rut=abc">LA Coders Monthly Meetup</a>
    </h2>
    <a class="result__snippet" href="https://www.meetup.com/la-coders/events/456/">A casual meetup for local developers.</a>
  </div>
</div>
"""

# A schema.org Event JSON-LD block as Meetup/Eventbrite/Luma embed on an
# event page, wrapped in the surrounding HTML a real page would have.
SAMPLE_EVENT_PAGE_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "LA Hikers & Outdoors Meetup",
  "startDate": "2026-10-03T17:00:00-07:00",
  "endDate": "2026-10-03T19:00:00-07:00",
  "location": {
    "@type": "Place",
    "name": "Griffith Park",
    "address": {"streetAddress": "4730 Crystal Springs Dr", "addressLocality": "Los Angeles", "addressRegion": "CA"}
  }
}
</script>
</head><body></body></html>
"""

SAMPLE_EVENT_PAGE_NO_JSONLD = "<html><body><p>No structured data here.</p></body></html>"


def _mock_response(text):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _event(idx, title, start, end, people=None, is_all_day=False, category="Social", location=None):
    return {
        "id": f"evt-{idx}", "title": title, "notes": None, "location": location,
        "startDate": start, "endDate": end, "isAllDay": is_all_day,
        "calendarTitle": category, "calendarColorHex": None,
        "attendees": people or [], "isRecurring": False, "url": None, "noteTags": {},
    }


@pytest.fixture
def df():
    events = [
        _event(1, "Trail run", "2026-09-05T17:00:00Z", "2026-09-05T19:00:00Z", people=["Alice"], category="Outdoors", location="Griffith Park"),
        _event(2, "Trail run", "2026-09-12T17:00:00Z", "2026-09-12T19:00:00Z", people=["Alice"], category="Outdoors", location="Griffith Park"),
        _event(3, "Trail run", "2026-09-19T17:00:00Z", "2026-09-19T19:00:00Z", people=["Alice", "Bob"], category="Outdoors", location="Griffith Park"),
        _event(4, "Dentist", "2026-10-03T18:00:00Z", "2026-10-03T18:30:00Z", category="Health"),
    ]
    return ingest.load_events_from_records(events)


def test_platform_events_rejects_unknown_platform():
    with pytest.raises(ValueError):
        future_events.platform_events("carrier-pigeon")


def test_platform_events_parses_and_filters_by_domain():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup", region="Los Angeles, CA")

    assert len(events) == 2  # the some-blog.com result is dropped
    assert events[0]["title"] == "LA Hikers & Outdoors Meetup"
    assert events[0]["url"] == "https://www.meetup.com/la-hikers/events/123/"
    assert events[0]["source"] == "meetup"
    assert events[0]["location"] == "Los Angeles, CA"  # falls back to region - no JSON-LD on this page
    assert events[0]["start"] is None
    assert "Griffith Park" in events[0]["snippet"]


def test_platform_events_unwraps_ddg_redirect_links():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup", region="Los Angeles, CA")

    redirected = next(e for e in events if "la-coders" in e["url"])
    assert redirected["url"] == "https://www.meetup.com/la-coders/events/456/"


def test_platform_events_query_includes_domain_and_region():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)) as mock_post, \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        future_events.platform_events("eventbrite", region="Austin, TX")

    query = mock_post.call_args.kwargs["data"]["q"]
    assert "site:eventbrite.com" in query
    assert "Austin, TX" in query


def test_platform_events_returns_empty_list_on_network_error():
    with patch("requests.post", side_effect=OSError("network unreachable")):
        assert future_events.platform_events("luma") == []


def test_platform_events_respects_max_results():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup", max_results=1)
    assert len(events) == 1


def test_platform_events_enriches_from_event_page_jsonld():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.platform_events("meetup", max_results=1)

    event = events[0]
    # startDate/endDate are given as -07:00 (Pacific); enrichment normalizes
    # to naive UTC, matching how felinni.ingest stores every other event's
    # start/end - so this becomes 2026-10-04T00:00/02:00, not the wall-clock
    # Pacific time.
    assert event["start"] == "2026-10-04T00:00:00"
    assert event["end"] == "2026-10-04T02:00:00"
    assert event["duration_hours"] == 2.0
    assert "Griffith Park" in event["location"]


def test_enrich_with_event_page_leaves_event_unchanged_on_fetch_failure():
    stub = {"title": "X", "url": "https://www.meetup.com/x", "start": None, "end": None,
            "duration_hours": None, "location": "Los Angeles, CA", "source": "meetup", "snippet": ""}
    with patch("requests.get", side_effect=OSError("network unreachable")):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched == stub


def test_calendar_conflicts_finds_overlapping_calendar_event(df):
    event = {"title": "Random Meetup", "start": "2026-10-03T18:15:00", "end": "2026-10-03T19:15:00"}
    conflicts = future_events.calendar_conflicts(event, df)
    assert len(conflicts) == 1
    assert conflicts[0]["title"] == "Dentist"


def test_calendar_conflicts_empty_when_no_overlap(df):
    event = {"title": "Random Meetup", "start": "2026-10-04T18:00:00", "end": "2026-10-04T19:00:00"}
    assert future_events.calendar_conflicts(event, df) == []


def test_calendar_conflicts_empty_without_parsed_time(df):
    assert future_events.calendar_conflicts({"title": "No time"}, df) == []


def test_event_conflicts_finds_overlap_between_candidates():
    a = {"title": "A", "url": "urlA", "start": "2026-10-03T18:00:00", "end": "2026-10-03T20:00:00"}
    b = {"title": "B", "url": "urlB", "start": "2026-10-03T19:00:00", "end": "2026-10-03T21:00:00", "source": "eventbrite"}
    assert future_events.event_conflicts(a, [a, b])[0]["title"] == "B"
    assert future_events.event_conflicts(b, [a, b])[0]["title"] == "A"


def test_annotate_conflicts_flags_both_calendar_and_event_conflicts(df):
    events = [
        {"title": "Meetup A", "url": "urlA", "start": "2026-10-03T18:15:00", "end": "2026-10-03T19:15:00", "source": "meetup"},
        {"title": "Meetup B", "url": "urlB", "start": "2026-10-03T18:30:00", "end": "2026-10-03T19:30:00", "source": "eventbrite"},
        {"title": "Meetup C", "url": "urlC", "start": "2026-11-01T18:00:00", "end": "2026-11-01T19:00:00", "source": "luma"},
    ]
    annotated = future_events.annotate_conflicts(events, df)
    a, b, c = annotated
    assert a["has_conflict"] is True
    assert {t["type"] for t in a["conflicts"]} == {"calendar", "event"}
    assert b["has_conflict"] is True
    assert c["has_conflict"] is False
    assert c["conflicts"] == []


def test_suggestions_for_is_empty_without_candidates(df):
    assert future_events.suggestions_for(df) == []
    assert future_events.suggestions_for(df, []) == []


def test_suggestions_for_ranks_matching_category_and_time_higher(df):
    # Saturdays 5pm, "Outdoors" in the title - matches the trail-run history closely.
    good_fit = {
        "title": "Outdoors trail hike", "url": "urlGood", "source": "meetup",
        "start": "2026-10-10T17:30:00", "end": "2026-10-10T19:00:00",
        "location": "Griffith Park Trailhead", "snippet": "",
    }
    # No category/time/location overlap with anything in the calendar.
    poor_fit = {
        "title": "Random Wine Tasting", "url": "urlPoor", "source": "eventbrite",
        "start": "2026-10-11T11:00:00", "end": "2026-10-11T12:00:00",
        "location": "Napa Valley", "snippet": "",
    }
    annotated = future_events.annotate_conflicts([good_fit, poor_fit], df)
    ranked = future_events.suggestions_for(df, annotated)

    assert [r["url"] for r in ranked] == ["urlGood", "urlPoor"]
    assert ranked[0]["matched_category"] == "Outdoors"
    assert ranked[0]["fit_score"] > ranked[1]["fit_score"]
    assert "Alice" in ranked[0]["suggested_people"]  # top trail-run companion


def test_suggestions_for_penalizes_conflicting_events(df):
    conflicting = {
        "title": "Random Meetup", "url": "urlConflict", "source": "meetup",
        "start": "2026-10-03T18:15:00", "end": "2026-10-03T19:15:00",
        "location": None, "snippet": "",
    }
    annotated = future_events.annotate_conflicts([conflicting], df)
    ranked = future_events.suggestions_for(df, annotated)
    assert ranked[0]["fit_score"] < 0
    assert any("conflict" in reason for reason in ranked[0]["fit_reasons"])


def test_suggested_people_falls_back_to_overall_frequency_for_unmatched_category(df):
    people = future_events.suggested_people(df, category=None)
    assert "Alice" in people
