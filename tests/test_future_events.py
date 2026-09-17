"""Tests for felinni.future_events: parsing/filtering DuckDuckGo HTML search
results and each event page's schema.org JSON-LD into the Future tab's event
shape, listing-page/occurrence-picking edge cases, conflict detection against
the calendar and other candidates, and ranking by fit with calendar history.
Network calls are always mocked via requests.post/requests.get (see
felinni.calendar_sources's tests for the same pattern) - these never hit a
real endpoint."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import future_events, ingest

# A trimmed but structurally faithful DuckDuckGo HTML results page: one
# meetup.com event page (should survive the domain+URL-shape filter), an
# Eventbrite-style "browse this whole city" listing page (should be
# dropped even though it matched the site: filter), and a result whose
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
      <a rel="nofollow" class="result__a" href="https://www.meetup.com/find/?location=la">Discover Los Angeles Events &amp; Activities</a>
    </h2>
    <a class="result__snippet" href="https://www.meetup.com/find/?location=la">Browse everything happening in LA.</a>
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

# A schema.org Event JSON-LD block as a real event page embeds for SEO,
# wrapped in the surrounding HTML a real page would have.
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


def _multi_date_page_html(dates):
    """A "multiple dates" listing embedding one Event JSON-LD block per
    occurrence, the way a real recurring-event page does."""
    scripts = "\n".join(
        f'<script type="application/ld+json">{{"@type": "Event", "name": "Perfume Making Class", '
        f'"startDate": "{start}", "endDate": "{end}", "location": {{"name": "Sunset Rooftop"}}}}</script>'
        for start, end in dates
    )
    return f"<html><head>{scripts}</head></html>"


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

    assert len(events) == 2  # the discover/browse listing page is dropped
    assert events[0]["title"] == "LA Hikers & Outdoors Meetup"
    assert events[0]["url"] == "https://www.meetup.com/la-hikers/events/123/"
    assert events[0]["source"] == "meetup"
    assert events[0]["location"] == "Los Angeles, CA"  # falls back to region - no JSON-LD on this page
    assert events[0]["start"] is None
    assert "Griffith Park" in events[0]["snippet"]


def test_platform_events_drops_a_browse_listing_page():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup")

    assert all("discover" not in e["title"].casefold() for e in events)
    assert all("find" not in e["url"] for e in events)


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


@pytest.mark.parametrize("platform,domain", [("partiful", "partiful.com"), ("posh", "posh.vip")])
def test_partiful_and_posh_query_their_own_domain(platform, domain):
    with patch("requests.post", return_value=_mock_response("")) as mock_post:
        future_events.platform_events(platform, region="Los Angeles, CA")

    query = mock_post.call_args.kwargs["data"]["q"]
    assert f"site:{domain}" in query


def test_partiful_result_is_kept_without_a_registered_url_pattern():
    """Partiful/Posh have no _EVENT_URL_PATTERNS entry (their exact
    event-URL shape isn't confirmed), so _is_event_url should default to
    allowing any result on the domain rather than filtering everything out."""
    ddg_html = """
    <div class="result"><div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://partiful.com/e/abc123">Someone's Birthday Party</a></h2>
      <a class="result__snippet" href="https://partiful.com/e/abc123">You're invited!</a>
    </div></div>
    """
    with patch("requests.post", return_value=_mock_response(ddg_html)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("partiful", region="Los Angeles, CA")

    assert len(events) == 1
    assert events[0]["source"] == "partiful"


def test_camber_query_scopes_to_the_la_happenings_section():
    with patch("requests.post", return_value=_mock_response("")) as mock_post:
        future_events.platform_events("camber", region="Los Angeles, CA")

    query = mock_post.call_args.kwargs["data"]["q"]
    assert "site:camberplaces.substack.com/s/la-happenings" in query


def test_camber_result_is_kept_even_without_a_confirmed_date():
    """Camber is a Substack roundup, not a per-event page - there's no
    schema.org Event markup to enrich it with, so (unlike other_web_events'
    domain-agnostic results) it shouldn't be dropped just for lacking a
    parsed date."""
    ddg_html = """
    <div class="result"><div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://camberplaces.substack.com/p/la-happenings-nov-3">LA Happenings, Nov 3</a></h2>
      <a class="result__snippet" href="https://camberplaces.substack.com/p/la-happenings-nov-3">This week's roundup of things to do.</a>
    </div></div>
    """
    with patch("requests.post", return_value=_mock_response(ddg_html)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("camber", region="Los Angeles, CA")

    assert len(events) == 1
    assert events[0]["source"] == "camber"
    assert events[0]["start"] is None
    assert events[0]["url"] == "https://camberplaces.substack.com/p/la-happenings-nov-3"


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


def test_enrich_with_event_page_falls_back_to_text_date_when_page_has_no_jsonld():
    stub = {
        "title": "American International Short Film Festival Tickets, Monday, September 14, 2026",
        "url": "https://www.eventbrite.com/e/short-film-festival-tickets-123",
        "start": None, "end": None, "duration_hours": None,
        "location": "Los Angeles, CA", "source": "eventbrite",
        "snippet": "Eventbrite - American International Short Film Festival presents ... "
                   "Monday, September 14, 2026 at City Club LA, Los Angeles, CA.",
    }
    with patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        enriched = future_events.enrich_with_event_page(stub)

    assert enriched["start"] is not None
    assert enriched["start"].startswith("2026-09-14")
    assert enriched["end"] is not None


def test_enrich_with_event_page_falls_back_to_text_date_when_fetch_fails():
    stub = {
        "title": "X", "url": "https://www.eventbrite.com/e/x",
        "start": None, "end": None, "duration_hours": None,
        "location": "Los Angeles, CA", "source": "eventbrite",
        "snippet": "Join us Monday, September 14, 2026 at 7:00PM for something fun.",
    }
    with patch("requests.get", side_effect=OSError("blocked")):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched["start"] is not None
    assert enriched["start"].startswith("2026-09-14")


def test_enrich_with_event_page_leaves_event_unchanged_when_no_date_anywhere():
    stub = {"title": "X", "url": "https://www.meetup.com/x", "start": None, "end": None,
            "duration_hours": None, "location": "Los Angeles, CA", "source": "meetup", "snippet": "no date here"}
    with patch("requests.get", side_effect=OSError("network unreachable")):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched == stub


def test_multi_date_listing_picks_the_soonest_future_occurrence_not_a_blended_span():
    """A "multiple dates" listing (e.g. a recurring class) embeds one Event
    JSON-LD block per occurrence. Previously this module took only the
    first node found and combined whichever start/end it had, which could
    accidentally pair one occurrence's start with a much later occurrence's
    end - reported as a single event spanning many months. It should
    instead pick one whole, self-consistent occurrence: the soonest one
    still in the future."""
    now = future_events._now_utc()
    past_start = now - pd.Timedelta(days=30)
    soon_start = now + pd.Timedelta(days=10)
    later_start = now + pd.Timedelta(days=200)
    dates = [
        (past_start.isoformat(), (past_start + pd.Timedelta(hours=2)).isoformat()),
        (soon_start.isoformat(), (soon_start + pd.Timedelta(hours=2)).isoformat()),
        (later_start.isoformat(), (later_start + pd.Timedelta(hours=2)).isoformat()),
    ]
    stub = {
        "title": "Perfume Making Class", "url": "https://www.eventbrite.com/e/perfume-class",
        "start": None, "end": None, "duration_hours": None,
        "location": "Los Angeles, CA", "source": "eventbrite", "snippet": "Multiple dates",
    }
    with patch("requests.get", return_value=_mock_response(_multi_date_page_html(dates))):
        enriched = future_events.enrich_with_event_page(stub)

    picked_start = pd.Timestamp(enriched["start"])
    assert abs((picked_start - soon_start).total_seconds()) < 1
    assert enriched["duration_hours"] == 2.0  # one coherent occurrence's own start+end, not a blended span


def test_implausibly_long_span_is_treated_as_unknown_end():
    stub = {
        "title": "X", "url": "https://www.eventbrite.com/e/x", "start": None, "end": None,
        "duration_hours": None, "location": "LA", "source": "eventbrite", "snippet": "",
    }
    page = _multi_date_page_html([("2026-08-14T03:00:00Z", "2027-03-24T05:00:00Z")])
    with patch("requests.get", return_value=_mock_response(page)):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched["start"] is not None
    assert enriched["end"] is None
    assert enriched["duration_hours"] is None


def test_other_web_events_skips_known_platform_domains():
    ddg_html = """
    <div class="result"><div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://www.meetup.com/x/events/1/">Meetup event</a></h2>
      <a class="result__snippet" href="https://www.meetup.com/x/events/1/">snippet</a>
    </div></div>
    """
    with patch("requests.post", return_value=_mock_response(ddg_html)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert events == []


def test_other_web_events_keeps_a_confirmed_real_event_from_any_domain():
    ddg_html = """
    <div class="result"><div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://www.residentadvisor.net/events/123">A real show</a></h2>
      <a class="result__snippet" href="https://www.residentadvisor.net/events/123">Live music tonight.</a>
    </div></div>
    """
    with patch("requests.post", return_value=_mock_response(ddg_html)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert len(events) == 1
    assert events[0]["source"] == "residentadvisor.net"
    assert events[0]["start"] is not None


def test_other_web_events_drops_results_with_no_confirmed_date():
    ddg_html = """
    <div class="result"><div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://www.some-blog.com/best-events">Best events roundup</a></h2>
      <a class="result__snippet" href="https://www.some-blog.com/best-events">A blog post, not an event page.</a>
    </div></div>
    """
    with patch("requests.post", return_value=_mock_response(ddg_html)), \
         patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert events == []


def test_ollama_event_ideas_returns_empty_when_ollama_unavailable(df):
    with patch("requests.post", side_effect=OSError("connection refused")):
        assert future_events.ollama_event_ideas(df) == []


def test_ollama_event_ideas_parses_response_lines(df):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": "- Try a new hiking trail\n- Visit a local museum\n"}
    with patch("requests.post", return_value=resp):
        ideas = future_events.ollama_event_ideas(df, limit=2)
    assert len(ideas) == 2
    assert ideas[0]["is_ai_suggestion"] is True
    assert ideas[0]["url"] is None
    assert "hiking trail" in ideas[0]["title"]


def test_ollama_event_ideas_empty_for_empty_calendar():
    assert future_events.ollama_event_ideas(pd.DataFrame()) == []


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


def test_event_conflicts_does_not_treat_two_urlless_ai_ideas_as_conflicting():
    a = {"title": "Idea A", "url": None, "start": None, "end": None}
    b = {"title": "Idea B", "url": None, "start": None, "end": None}
    assert future_events.event_conflicts(a, [a, b]) == []


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
    assert "Outdoors" in ranked[0]["people_reason"]


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


def test_suggested_people_uses_category_when_matched(df):
    people, reason = future_events.suggested_people(df, category="Outdoors")
    assert "Alice" in people
    assert "Outdoors" in reason


def test_suggested_people_falls_back_to_location_when_no_category(df):
    people, reason = future_events.suggested_people(df, category=None, location="Griffith Park Trailhead")
    assert "Alice" in people
    assert "Griffith Park" in reason


def test_suggested_people_falls_back_to_overall_frequency_when_nothing_matches(df):
    people, reason = future_events.suggested_people(df, category=None, location=None)
    assert "Alice" in people
    assert "overall" in reason


def test_suggested_people_empty_for_empty_calendar():
    people, reason = future_events.suggested_people(pd.DataFrame(), category="Outdoors")
    assert people == []
