"""Tests for felinni.future_events: turning ddgs search results and each
event page's schema.org JSON-LD into the Future tab's event shape,
listing-page/occurrence-picking edge cases, conflict detection against the
calendar and other candidates, and ranking by fit with calendar history.
Event discovery (`_ddg_text_search`) is mocked via `ddgs.DDGS` (see
`_mock_ddgs`); event-page enrichment (`_fetch_page`) still goes through
`requests.get`, and `ollama_event_ideas` through `requests.post` - neither
of these ever hits a real endpoint in these tests."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import future_events, ingest

# One real meetup.com event (should survive the domain+URL-shape filter), an
# Eventbrite-style "browse this whole city" listing page (should be dropped
# even though it matched the site: filter), and a second real event - the
# shape `ddgs.DDGS().text()` returns: {title, href, body}.
SAMPLE_SEARCH_RESULTS = [
    {
        "title": "LA Hikers & Outdoors Meetup",
        "href": "https://www.meetup.com/la-hikers/events/123/",
        "body": "Join us for a sunrise hike in Griffith Park this Saturday.",
    },
    {
        "title": "Discover Los Angeles Events & Activities",
        "href": "https://www.meetup.com/find/?location=la",
        "body": "Browse everything happening in LA.",
    },
    {
        "title": "LA Coders Monthly Meetup",
        "href": "https://www.meetup.com/la-coders/events/456/",
        "body": "A casual meetup for local developers.",
    },
]

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


def _mock_ddgs(results=None, side_effect=None):
    """A patcher for `ddgs.DDGS` (imported lazily inside
    `_ddg_text_search` as `from ddgs import DDGS`) - `DDGS(timeout=...)`
    returns a fake instance whose `.text(query, max_results=...)` returns
    `results` (or raises `side_effect`). Returns (patcher, mock_instance)
    so a test can both use it as a context manager and inspect
    `mock_instance.text.call_args` afterward."""
    mock_instance = MagicMock()
    if side_effect is not None:
        mock_instance.text.side_effect = side_effect
    else:
        mock_instance.text.return_value = results if results is not None else []
    mock_class = MagicMock(return_value=mock_instance)
    return patch("ddgs.DDGS", mock_class), mock_instance


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
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup", region="Los Angeles, CA")

    assert len(events) == 2  # the discover/browse listing page is dropped
    assert events[0]["title"] == "LA Hikers & Outdoors Meetup"
    assert events[0]["url"] == "https://www.meetup.com/la-hikers/events/123/"
    assert events[0]["source"] == "meetup"
    assert events[0]["location"] == "Los Angeles, CA"  # falls back to region - no JSON-LD on this page
    assert events[0]["start"] is None
    assert "Griffith Park" in events[0]["snippet"]


def test_platform_events_drops_a_browse_listing_page():
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup")

    assert all("discover" not in e["title"].casefold() for e in events)
    assert all("find" not in e["url"] for e in events)


def test_platform_events_query_includes_domain_and_region():
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events.platform_events("eventbrite", region="Austin, TX")

    query = mock_instance.text.call_args.args[0]
    assert "site:eventbrite.com" in query
    assert "Austin, TX" in query
    assert "this week" in query  # default days_ahead=7 -> "this week" phrase


def test_platform_events_returns_empty_list_on_network_error():
    patcher, _ = _mock_ddgs(side_effect=OSError("network unreachable"))
    with patcher, patch("time.sleep"):
        assert future_events.platform_events("luma") == []


def test_platform_events_debug_records_search_failure():
    debug = {}
    patcher, _ = _mock_ddgs(side_effect=OSError("network unreachable"))
    with patcher, patch("time.sleep"):
        future_events.platform_events("luma", debug=debug)
    assert "search failed" in debug["luma"]
    assert "network unreachable" in debug["luma"]


def test_platform_events_debug_records_zero_results():
    debug = {}
    patcher, _ = _mock_ddgs([])
    with patcher:
        future_events.platform_events("luma", debug=debug)
    assert debug["luma"] == "search returned 0 results"


def test_platform_events_debug_records_counts_when_results_are_filtered():
    debug = {}
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        future_events.platform_events("meetup", debug=debug)
    assert "result(s)" in debug["meetup"]
    assert "kept" in debug["meetup"]


def test_platform_events_searches_exactly_once_per_call():
    """A prior version retried with a second, broader query whenever the
    first came back empty - on top of the webapp layer already searching
    every source twice - and a real run showed exactly why that's the
    wrong direction: quadrupling request volume across ~7 sources in
    under two minutes reads as scraping abuse to the search backends,
    not politeness, and tripped what looked like a rate limit/temporary
    block (every query after the first failed outright, in that run and
    in a fresh run started minutes later). One query per call, full stop."""
    mock_instance = MagicMock()
    mock_instance.text.return_value = []
    with patch("ddgs.DDGS", MagicMock(return_value=mock_instance)):
        future_events.platform_events("meetup", max_results=6)

    assert mock_instance.text.call_count == 1


def test_other_web_events_debug_uses_web_key():
    debug = {}
    patcher, _ = _mock_ddgs(side_effect=OSError("network unreachable"))
    with patcher, patch("time.sleep"):
        future_events.other_web_events(debug=debug)
    assert "web" in debug
    assert "search failed" in debug["web"]


@pytest.mark.parametrize("days_ahead,expected_phrase", [(1, "today"), (7, "this week"), (30, "this month"), (365, "upcoming")])
def test_time_window_phrase_scales_with_days_ahead(days_ahead, expected_phrase):
    assert future_events._time_window_phrase(days_ahead) == expected_phrase


def test_platform_events_query_reflects_a_custom_days_ahead():
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events.platform_events("eventbrite", days_ahead=30)

    query = mock_instance.text.call_args.args[0]
    assert "this month" in query


def test_platform_events_overfetches_relative_to_max_results():
    """More raw results are pulled than `max_results` asks for, since
    filtering (listing pages, wrong domain) drops some of what comes
    back - see _SEARCH_OVERFETCH_FACTOR."""
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events.platform_events("eventbrite", max_results=6)
    assert mock_instance.text.call_args.kwargs["max_results"] > 6


def test_within_search_window_drops_events_confirmed_beyond_the_window():
    now = future_events._now_local()
    soon = {"title": "Soon", "start": (now + pd.Timedelta(days=2)).isoformat(), "end": None}
    far = {"title": "Far", "start": (now + pd.Timedelta(days=60)).isoformat(), "end": None}
    unknown = {"title": "Unknown", "start": None, "end": None}

    kept = future_events.within_search_window([soon, far, unknown], days_ahead=7)

    assert {e["title"] for e in kept} == {"Soon", "Unknown"}


def test_within_search_window_keeps_something_that_started_very_recently():
    now = future_events._now_local()
    just_started = {"title": "Just started", "start": (now - pd.Timedelta(hours=2)).isoformat(), "end": None}
    assert future_events.within_search_window([just_started], days_ahead=7) == [just_started]


@pytest.mark.parametrize("platform,domain", [("partiful", "partiful.com"), ("posh", "posh.vip")])
def test_partiful_and_posh_query_their_own_domain(platform, domain):
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events.platform_events(platform, region="Los Angeles, CA")

    query = mock_instance.text.call_args.args[0]
    assert f"site:{domain}" in query


def test_partiful_result_at_a_real_event_url_is_kept():
    results = [{"title": "Someone's Birthday Party", "href": "https://partiful.com/e/abc123", "body": "You're invited!"}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("partiful", region="Los Angeles, CA")

    assert len(events) == 1
    assert events[0]["source"] == "partiful"


def test_partiful_account_landing_page_is_filtered_out():
    """Confirmed directly: a Partiful account/group's own landing page
    ("Los Angeles Fun Events - Partiful", describing the kind of events a
    host runs rather than one specific happening) is not at partiful.com/e/
    and should be filtered, not returned as if it were a real event."""
    results = [{
        "title": "Los Angeles Fun Events - Partiful",
        "href": "https://partiful.com/l/los-angeles-fun-events",
        "body": "We host variety of Speed dating, Mixers, Picnics and Social gathering events.",
    }]
    patcher, _ = _mock_ddgs(results)
    with patcher:
        events = future_events.platform_events("partiful", region="Los Angeles, CA")
    assert events == []


def test_posh_result_is_kept_without_a_registered_url_pattern():
    """Posh has no _EVENT_URL_PATTERNS entry (its exact event-URL shape
    isn't confirmed), so _is_event_url should default to allowing any
    result on the domain rather than filtering everything out."""
    results = [{"title": "KELELA NIGHT @ YOU Los Angeles - Posh", "href": "https://posh.vip/e/kelela-night", "body": "RSVP to KELELA NIGHT."}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("posh", region="Los Angeles, CA")

    assert len(events) == 1
    assert events[0]["source"] == "posh"


def test_camber_query_scopes_to_the_la_happenings_section():
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events.platform_events("camber", region="Los Angeles, CA")

    query = mock_instance.text.call_args.args[0]
    assert "site:camberplaces.substack.com/s/la-happenings" in query


def test_camber_result_is_kept_even_without_a_confirmed_date():
    """Camber is a Substack roundup, not a per-event page - there's no
    schema.org Event markup to enrich it with, so (unlike other_web_events'
    domain-agnostic results) it shouldn't be dropped just for lacking a
    parsed date."""
    results = [{
        "title": "LA Happenings Roundup",
        "href": "https://camberplaces.substack.com/p/la-happenings-roundup",
        "body": "This week's roundup of things to do.",
    }]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("camber", region="Los Angeles, CA")

    assert len(events) == 1
    assert events[0]["source"] == "camber"
    assert events[0]["start"] is None
    assert events[0]["url"] == "https://camberplaces.substack.com/p/la-happenings-roundup"


def test_platform_events_respects_max_results():
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("meetup", max_results=1)
    assert len(events) == 1


def test_platform_events_enriches_from_event_page_jsonld():
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.platform_events("meetup", max_results=1)

    event = events[0]
    # startDate/endDate are given as -07:00 (Pacific) - kept as the venue's
    # own wall-clock time (5pm/7pm), not converted to UTC: the frontend's
    # `new Date(isoString)` parses a timezone-less string as local time,
    # so converting to UTC here would silently shift the displayed time by
    # the venue's offset once re-interpreted as if already local.
    assert event["start"] == "2026-10-03T17:00:00"
    assert event["end"] == "2026-10-03T19:00:00"
    assert event["duration_hours"] == 2.0
    assert "Griffith Park" in event["location"]


def test_ddg_text_search_drops_results_with_no_href():
    """A malformed/ad result missing an href shouldn't blow up
    downstream code that assumes every result has a usable url."""
    patcher, _ = _mock_ddgs([{"title": "No link", "href": "", "body": "..."}])
    with patcher:
        results = future_events._ddg_text_search("test query", max_results=5)
    assert results == []


def test_ddg_text_search_prints_the_query_and_result_count(capsys):
    patcher, _ = _mock_ddgs(SAMPLE_SEARCH_RESULTS)
    with patcher:
        future_events._ddg_text_search("site:eventbrite.com Los Angeles events", max_results=5)
    out = capsys.readouterr().out
    assert "site:eventbrite.com Los Angeles events" in out
    assert "3 result" in out


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
    assert enriched == {**stub, "date_confirmed": False}


def test_multi_date_listing_picks_the_soonest_future_occurrence_not_a_blended_span():
    """A "multiple dates" listing (e.g. a recurring class) embeds one Event
    JSON-LD block per occurrence. Previously this module took only the
    first node found and combined whichever start/end it had, which could
    accidentally pair one occurrence's start with a much later occurrence's
    end - reported as a single event spanning many months. It should
    instead pick one whole, self-consistent occurrence: the soonest one
    still in the future."""
    now = future_events._now_local()
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
    results = [{"title": "Meetup event", "href": "https://www.meetup.com/x/events/1/", "body": "snippet"}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert events == []


def test_other_web_events_keeps_a_confirmed_real_event_from_any_domain():
    results = [{"title": "A real show", "href": "https://www.residentadvisor.net/events/123", "body": "Live music tonight."}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert len(events) == 1
    assert events[0]["source"] == "residentadvisor.net"
    assert events[0]["start"] is not None


def test_other_web_events_drops_results_with_no_confirmed_date():
    results = [{"title": "Best events roundup", "href": "https://www.some-blog.com/best-events", "body": "A blog post, not an event page."}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
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
    assert "recently" in reason  # the fixture's history is all within the last 180 days


def test_suggested_people_prefers_recent_company_over_old_history():
    """Someone you saw constantly two years ago but haven't since
    shouldn't keep outranking who you're actually spending time with now."""
    events = [
        _event(1, "Old friend hangout", "2023-01-05T17:00:00Z", "2023-01-05T19:00:00Z", people=["OldFriend"]),
        _event(2, "Old friend hangout", "2023-01-12T17:00:00Z", "2023-01-12T19:00:00Z", people=["OldFriend"]),
        _event(3, "Old friend hangout", "2023-01-19T17:00:00Z", "2023-01-19T19:00:00Z", people=["OldFriend"]),
        _event(4, "Recent hangout", "2026-09-01T17:00:00Z", "2026-09-01T19:00:00Z", people=["NewFriend"]),
    ]
    recent_df = ingest.load_events_from_records(events)
    people, reason = future_events.suggested_people(recent_df, category=None, location=None)
    # NewFriend still leads (your recent company); OldFriend is blended
    # in as a reconnection nudge - a real history (3+ events), just a
    # stale one - rather than dropped entirely.
    assert people[0] == "NewFriend"
    assert "OldFriend" in people
    assert "recently" in reason
    assert "haven't seen" in reason


def test_suggested_people_falls_back_to_all_time_when_nothing_recent(df):
    """When there's no history at all in the recent window, falls back to
    all-time frequency rather than returning nothing."""
    old_events = [
        _event(1, "Old hangout", "2020-01-05T17:00:00Z", "2020-01-05T19:00:00Z", people=["OldFriend"]),
    ]
    old_df = ingest.load_events_from_records(old_events)
    people, reason = future_events.suggested_people(old_df, category=None, location=None)
    assert people == ["OldFriend"]
    assert reason == "your most frequent people overall"


def test_suggested_people_empty_for_empty_calendar():
    people, reason = future_events.suggested_people(pd.DataFrame(), category="Outdoors")
    assert people == []


def test_matched_frequent_location_prefers_a_recently_visited_place():
    events = [
        _event(1, "Old visit", "2020-01-05T17:00:00Z", "2020-01-05T19:00:00Z", location="Griffith Park Old Spot"),
        _event(2, "Recent visit", "2026-09-01T17:00:00Z", "2026-09-01T19:00:00Z", location="Griffith Park New Spot"),
    ]
    df = ingest.load_events_from_records(events)
    assert future_events._matched_frequent_location(df, "Griffith Park") == "Griffith Park New Spot"


def test_dedupe_events_collapses_the_same_event_from_two_domains():
    """The same real event ("Country 2 Step Lesson") independently listed
    on allevents.in and stayhappening.com - same title/address, close but
    not identical start timestamps and different URLs - should collapse
    to one entry, keeping whichever came first."""
    a = {
        "title": "Country 2 Step Lesson", "url": "https://allevents.in/x",
        "start": "2026-09-24T00:00:00", "end": "2026-09-24T00:00:00",
        "location": "1321 E Thousand Oaks Blvd, Thousand Oaks, CA",
        "source": "allevents.in", "snippet": "",
    }
    b = {
        "title": "Country 2 Step Lesson", "url": "https://stayhappening.com/y",
        "start": "2026-09-24T00:05:00", "end": "2026-09-24T00:05:00",
        "location": "1321 E Thousand Oaks Blvd, Thousand Oaks, CA",
        "source": "stayhappening.com", "snippet": "",
    }
    deduped = future_events.dedupe_events([a, b])
    assert len(deduped) == 1
    assert deduped[0]["url"] == "https://allevents.in/x"


def test_dedupe_events_keeps_different_events_with_the_same_title():
    a = {"title": "Weekly Meetup", "url": "urlA", "start": "2026-09-24T00:00:00", "end": None, "location": "Venue A"}
    b = {"title": "Weekly Meetup", "url": "urlB", "start": "2026-10-01T00:00:00", "end": None, "location": "Venue B"}
    assert len(future_events.dedupe_events([a, b])) == 2


def test_dedupe_events_falls_back_to_location_when_start_is_unknown():
    a = {"title": "Mystery Event", "url": "urlA", "start": None, "end": None, "location": "Same Venue"}
    b = {"title": "Mystery Event", "url": "urlB", "start": None, "end": None, "location": "Same Venue"}
    c = {"title": "Mystery Event", "url": "urlC", "start": None, "end": None, "location": "Different Venue"}
    deduped = future_events.dedupe_events([a, b, c])
    assert len(deduped) == 2
    assert {e["url"] for e in deduped} == {"urlA", "urlC"}


def test_clean_address_text_collapses_exact_duplicate_segments():
    garbled = (
        "1321 E Thousand Oaks Blvd. #108 , Thousand Oaks, CA, United States, "
        "California 91362, 1321 E Thousand Oaks Blvd, Thousand Oaks, CA "
        "91362-2821, United States, Thousand Oaks, CA"
    )
    cleaned = future_events._clean_address_text(garbled)
    # "Thousand Oaks, CA" and "United States" each appeared 3x/2x - collapsed to one each.
    assert cleaned.count("Thousand Oaks, CA") == 1
    assert cleaned.count("United States") == 1
    assert "1321 E Thousand Oaks Blvd. #108" in cleaned


def test_location_from_jsonld_cleans_a_duplicated_address_string():
    garbled = "123 Main St, Springfield, IL, 123 Main St, Springfield, IL"
    assert future_events._location_from_jsonld(garbled) == "123 Main St, Springfield, IL"


def test_is_bare_domain_root_true_for_homepage_false_for_a_real_path():
    assert future_events._is_bare_domain_root("https://www.eventbrite.com/") is True
    assert future_events._is_bare_domain_root("https://camberplaces.substack.com") is True
    assert future_events._is_bare_domain_root("https://www.eventbrite.com/e/some-event-tickets-123") is False


def test_looks_like_listing_catches_platform_browse_page_titles():
    """Real browse-page titles observed directly, none of which matched
    the original "Discover ... Events" pattern: a platform's own SEO
    title for its city landing page, and a domain-agnostic aggregator's
    "all events" page."""
    assert future_events._looks_like_listing("Thousand Oaks Events, Tickets & Things to Do | Eventbrite")
    assert future_events._looks_like_listing("All Upcoming events in Thousand Oaks")
    assert not future_events._looks_like_listing("Country 2 Step Lesson")


def test_platform_events_skips_the_platforms_own_homepage():
    """A `site:` search's top "result" can be the domain's own homepage
    rather than any specific event - this is never a real event, for any
    platform, including ones with no _EVENT_URL_PATTERNS entry (which
    would otherwise default to allowing any URL on the domain)."""
    results = [{"title": "LA Happenings - Camber", "href": "https://camberplaces.substack.com", "body": "..."}]
    patcher, _ = _mock_ddgs(results)
    with patcher:
        events = future_events.platform_events("camber", region="Los Angeles, CA")
    assert events == []


def test_other_web_events_skips_a_bare_domain_root():
    results = [{"title": "Some Ticketing Site", "href": "https://www.someticketsite.com/", "body": "..."}]
    patcher, _ = _mock_ddgs(results)
    with patcher:
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert events == []


def test_strip_platform_suffix_drops_trailing_branding():
    assert future_events._strip_platform_suffix("Los Angeles Fun Events - Partiful", "partiful") == "Los Angeles Fun Events"
    assert future_events._strip_platform_suffix("KELELA NIGHT @ YOU Los Angeles - Posh", "posh") == "KELELA NIGHT @ YOU Los Angeles"
    assert future_events._strip_platform_suffix("LA Hikers & Outdoors Meetup", "meetup") == "LA Hikers & Outdoors Meetup"


def test_clean_snippet_collapses_whitespace_and_repeated_punctuation():
    raw = "FOR FREE ENTRANCE!!!   Location   4574 Beverly Blvd???"
    cleaned = future_events._clean_snippet(raw)
    assert "!!!" not in cleaned
    assert "???" not in cleaned
    assert "  " not in cleaned


def test_clean_snippet_caps_length():
    long_snippet = "word " * 100
    cleaned = future_events._clean_snippet(long_snippet)
    assert len(cleaned) <= 221
    assert cleaned.endswith("…")


def test_platform_events_strips_branding_suffix_from_title():
    results = [{"title": "Los Angeles Fun Events - Partiful", "href": "https://partiful.com/e/la-fun-events", "body": "..."}]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("partiful", region="Los Angeles, CA")
    assert events[0]["title"] == "Los Angeles Fun Events"


def test_fallback_datetime_parses_weekday_numeric_date_and_time():
    parsed = future_events._fallback_datetime_from_text("Rooftop Pool Party Labor Day WeekendSat 9/5 at 3pm.")
    assert parsed is not None
    assert parsed.month == 9 and parsed.day == 5 and parsed.hour == 15


def test_fallback_datetime_parses_weekday_month_day_no_year():
    parsed = future_events._fallback_datetime_from_text("VP to KELELA NIGHT. Thu, May 7 at 9:00 PM - 2:00 AM YOU.")
    assert parsed is not None
    assert parsed.month == 5 and parsed.day == 7 and parsed.hour == 21


def test_fallback_datetime_still_parses_full_month_day_year():
    parsed = future_events._fallback_datetime_from_text("Monday, September 14, 2026 at 7:00PM for something fun.")
    assert parsed == pd.Timestamp("2026-09-14T19:00:00")


def test_fallback_datetime_date_only_defaults_to_midnight():
    parsed = future_events._fallback_datetime_from_text(
        "American International Short Film Festival Tickets, Monday, September 14, 2026"
    )
    assert parsed == pd.Timestamp("2026-09-14T00:00:00")


def test_fallback_datetime_none_when_no_date_present():
    assert future_events._fallback_datetime_from_text("no date here at all") is None


def test_enrich_with_event_page_sets_date_confirmed_true_for_jsonld():
    stub = {"title": "X", "url": "https://www.eventbrite.com/e/x", "start": None, "end": None,
            "duration_hours": None, "location": "LA", "source": "eventbrite", "snippet": ""}
    with patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_HTML)):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched["date_confirmed"] is True


def test_enrich_with_event_page_sets_date_confirmed_false_for_text_fallback():
    stub = {
        "title": "X", "url": "https://www.eventbrite.com/e/x", "start": None, "end": None,
        "duration_hours": None, "location": "LA", "source": "eventbrite",
        "snippet": "Join us Monday, September 14, 2026 at 7:00PM.",
    }
    with patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        enriched = future_events.enrich_with_event_page(stub)
    assert enriched["date_confirmed"] is False
    assert enriched["start"] is not None


def test_other_web_events_drops_a_plain_text_only_date_on_an_unknown_domain():
    """A plain-text date guess is far more likely to be a false positive
    on an arbitrary, untrusted domain (a random date elsewhere on the
    page) than on a page already confirmed to be a known event platform -
    other_web_events should require the stronger schema.org-confirmed
    signal, not just any parseable date-shaped text."""
    results = [{
        "title": "Luma (@luma_hq) • Instagram photos and videos",
        "href": "https://www.instagram.com/luma_hq/",
        "body": "Your curated guide to the best events in Los Angeles this weekend.",
    }]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.other_web_events(region="Los Angeles, CA")
    assert events == []


def test_fallback_datetime_does_not_treat_a_random_capitalized_word_as_a_month():
    """"Camber | Mady Maio - Substack 9/15: A Dolly Parton..." - the
    original broadened month/day pattern matched ANY capitalized word
    followed by a number ("Substack 9") as if "Substack" were a month
    name, producing a bogus date instead of reaching the real "9/15" a
    few words later. Only real month names should match."""
    title = "LA Happenings | Camber | Mady Maio - Substack"
    snippet = "9/15 - 9/21: A Dolly Parton tribute concert, 9/14: Babe's pancake tour"
    parsed = future_events._fallback_datetime_from_text(f"{title} {snippet}")
    assert parsed is not None
    assert parsed.month == 9 and parsed.day == 15


def test_looks_like_listing_does_not_flag_a_real_event_titled_with_event_in():
    """"events? in" alone is too broad - a real single event can
    legitimately be titled "Speed Dating Event in Los Angeles"."""
    assert not future_events._looks_like_listing("Speed Dating Event in Los Angeles")
    assert future_events._looks_like_listing("All Upcoming events in Thousand Oaks")


def test_looks_like_listing_does_not_flag_a_real_promotional_handle_tag():
    """A bare "(@handle)" is too broad - a real event can legitimately
    tag a promotional handle in its own title. Only flagged alongside an
    explicit platform name (the actual social-profile-mirror shape)."""
    assert not future_events._looks_like_listing("Live Show ft. DJ Snake (@djsnake)")
    assert future_events._looks_like_listing("Luma (@luma_hq) • Instagram photos and videos")


def test_camber_result_gets_the_first_date_out_of_a_multi_date_roundup():
    results = [{
        "title": "LA Happenings | Camber | Mady Maio - Substack",
        "href": "https://camberplaces.substack.com/p/la-happenings",
        "body": "9/15 - 9/21: A Dolly Parton tribute concert, 9/14: Babe's pancake tour",
    }]
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(SAMPLE_EVENT_PAGE_NO_JSONLD)):
        events = future_events.platform_events("camber", region="Los Angeles, CA")
    assert len(events) == 1
    assert events[0]["start"] is not None
    assert events[0]["start"].startswith("2026-09-15")


def test_location_fit_ignores_an_unconfirmed_generic_region_location():
    """An event whose location is just the fallback search region (no
    real venue - date_confirmed/location_confirmed both False) shouldn't
    substring-match a specific address in your history and claim
    proximity that isn't real."""
    event = {"location": "Los Angeles, CA", "location_confirmed": False}
    df = pd.DataFrame({"location": ["747 S Mansfield Ave Los Angeles, CA, United States"]})
    score, reasons = future_events._location_fit(df, event)
    assert score == 0.0
    assert reasons == []


def test_location_fit_uses_a_confirmed_specific_venue(df):
    event = {"location": "Griffith Park", "location_confirmed": True}
    score, reasons = future_events._location_fit(df, event)
    assert score == 1.0
    assert "Griffith Park" in reasons[0]


def test_suggestions_for_does_not_use_an_unconfirmed_location_for_people(df):
    """Regression: a Camber-style event with no real venue (location is
    just the generic region) previously still fed that generic location
    into suggested_people, which could substring-match an unrelated
    specific address from your history and wrongly claim "who you've
    recently gone to <address> with"."""
    event = {
        "title": "Roundup", "url": "urlX", "source": "camber",
        "start": None, "end": None, "location": "Los Angeles, CA",
        "location_confirmed": False, "snippet": "",
    }
    annotated = future_events.annotate_conflicts([event], df)
    ranked = future_events.suggestions_for(df, annotated)
    assert "recently gone to" not in ranked[0]["people_reason"]
    assert "usually go to" not in ranked[0]["people_reason"]


def test_location_keywords_extracts_street_name_ignoring_boilerplate():
    assert future_events._location_keywords("575 S Fairfax Ave, Los Angeles, CA 90036") == {"fairfax"}
    assert future_events._location_keywords("419 N Fairfax Ave, Los Angeles, CA 90036") == {"fairfax"}
    assert future_events._location_keywords("7312 Beverly Blvd, Los Angeles, CA") == {"beverly"}


def test_matched_frequent_location_matches_same_street_different_venue():
    """A brand-new venue you've never been to ("Molly Malone's, 575 S
    Fairfax Ave...") should still credit you with a nearby place you
    actually go ("El Coyote, 419 N Fairfax Ave...") - same street, not
    the same exact address."""
    events = [
        _event(1, "Dinner", "2026-09-01T19:00:00Z", "2026-09-01T21:00:00Z", location="El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"),
    ]
    df = ingest.load_events_from_records(events)
    matched = future_events._matched_frequent_location(df, "Molly Malone's, 575 S Fairfax Ave, Los Angeles, CA 90036")
    assert matched == "El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"


def test_matched_frequent_location_does_not_match_an_unrelated_street():
    events = [
        _event(1, "Dinner", "2026-09-01T19:00:00Z", "2026-09-01T21:00:00Z", location="Some Place, 7312 Beverly Blvd, Los Angeles, CA"),
    ]
    df = ingest.load_events_from_records(events)
    assert future_events._matched_frequent_location(df, "575 S Fairfax Ave, Los Angeles, CA 90036") is None


def test_suggested_people_recommends_neighborhood_company_for_a_new_venue():
    events = [
        _event(1, "Dinner", "2026-09-01T19:00:00Z", "2026-09-01T21:00:00Z", people=["FairfaxFriend"], location="El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"),
        _event(2, "Dinner", "2026-09-08T19:00:00Z", "2026-09-08T21:00:00Z", people=["FairfaxFriend"], location="El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"),
    ]
    df = ingest.load_events_from_records(events)
    people, reason = future_events.suggested_people(df, category=None, location="Molly Malone's, 575 S Fairfax Ave, Los Angeles, CA 90036")
    assert "FairfaxFriend" in people
    assert "El Coyote" in reason


def test_ddg_text_search_prints_on_failure_not_just_success(capsys):
    """A raised exception previously skipped past the success print
    entirely, leaving no visible trace in the terminal of why a
    particular search failed."""
    patcher, _ = _mock_ddgs(side_effect=RuntimeError("all backends rate-limited"))
    with patcher, patch("time.sleep"):
        with pytest.raises(RuntimeError):
            future_events._ddg_text_search("some query", max_results=5)
    out = capsys.readouterr().out
    assert "search failed" in out
    assert "all backends rate-limited" in out


def test_ddg_text_search_restricts_to_a_fixed_backend_list():
    """`ddgs`'s own "auto" backend mode shuffles through ALL registered
    engines (including Wikipedia/Grokipedia, which can't satisfy a
    `site:`-scoped web search at all) in a different order every call -
    confirmed directly as the cause of the same query returning a
    completely different result set (and sometimes an outright "no
    results found" failure) from one run to the next. A fixed backend
    list avoids that."""
    patcher, mock_instance = _mock_ddgs([])
    with patcher:
        future_events._ddg_text_search("some query", max_results=5)
    assert mock_instance.text.call_args.kwargs["backend"] == future_events._SEARCH_BACKENDS
    assert "wikipedia" not in future_events._SEARCH_BACKENDS
    assert "grokipedia" not in future_events._SEARCH_BACKENDS


def test_looks_like_listing_catches_a_popular_events_roundup_title():
    assert future_events._looks_like_listing("Popular events in Los Angeles")
    assert not future_events._looks_like_listing("KELELA NIGHT @ YOU Los Angeles")


def test_ddg_text_search_retries_once_after_a_failure(capsys):
    """The exact same query that raised ddgs's own "No results found."
    one run returned real results a moment later in the same session
    (observed directly) - a passing rate-limit/timeout hiccup, not a
    stable "nothing exists for this query". One retry after a short
    pause is what actually recovers from that."""
    call_count = {"n": 0}

    def flaky_text(query, max_results=None, backend=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("No results found.")
        return SAMPLE_SEARCH_RESULTS

    patcher, mock_instance = _mock_ddgs()
    mock_instance.text.side_effect = flaky_text
    with patcher:
        with patch("time.sleep"):
            results = future_events._ddg_text_search("some query", max_results=5)

    assert call_count["n"] == 2
    assert len(results) == 3


def test_ddg_text_search_raises_after_two_failed_attempts():
    patcher, _ = _mock_ddgs(side_effect=RuntimeError("still failing"))
    with patcher:
        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="still failing"):
                future_events._ddg_text_search("some query", max_results=5)


def test_parse_jsonld_datetime_keeps_venue_wall_clock_time_not_utc():
    """Confirmed directly against a real result: "Rapid Skateboarding"
    with a JSON-LD startDate of "2026-09-18T20:00:00-07:00" (8pm Pacific)
    displayed as "Sep 19 at 3:00 AM" - the previous behavior converted to
    UTC (00:00 the next day, in this case actually 03:00 since the
    example below uses a later evening time) and stored that number as a
    timezone-less string, which the frontend's `new Date(isoString)` then
    re-parsed as if it were already local time, silently shifting the
    displayed time by the venue's UTC offset. The wall-clock time as
    written should be kept unchanged."""
    parsed = future_events._parse_jsonld_datetime("2026-09-18T20:00:00-07:00")
    assert parsed == pd.Timestamp("2026-09-18T20:00:00")


def test_parse_jsonld_datetime_handles_a_naive_datetime_with_no_offset():
    parsed = future_events._parse_jsonld_datetime("2026-09-18T20:00:00")
    assert parsed == pd.Timestamp("2026-09-18T20:00:00")


def test_platform_events_end_to_end_keeps_venue_local_time():
    """Regression test for the exact reported case: a Partiful result
    whose page has JSON-LD with an explicit Pacific offset should show
    the venue's own evening time, not a shifted early-morning time on
    the following day."""
    results = [{"title": "Rapid Skateboarding", "href": "https://partiful.com/e/rapid-skateboarding", "body": "..."}]
    page_html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Event", "name": "Rapid Skateboarding",
     "startDate": "2026-09-18T20:00:00-07:00", "endDate": "2026-09-18T21:00:00-07:00",
     "location": {"name": "Rapid Skateboarding", "address": "1784 E Los Angeles Ave, Simi Valley, CA 93065"}}
    </script>
    </head></html>
    """
    patcher, _ = _mock_ddgs(results)
    with patcher, patch("requests.get", return_value=_mock_response(page_html)):
        events = future_events.platform_events("partiful", region="Los Angeles, CA")
    assert len(events) == 1
    assert events[0]["start"] == "2026-09-18T20:00:00"
    assert events[0]["end"] == "2026-09-18T21:00:00"


def test_most_overdue_person_picks_the_stalest_real_relationship():
    events = [
        _event(1, "A", "2023-01-01T17:00:00Z", "2023-01-01T19:00:00Z", people=["StaleFriend"]),
        _event(2, "A", "2023-02-01T17:00:00Z", "2023-02-01T19:00:00Z", people=["StaleFriend"]),
        _event(3, "A", "2023-03-01T17:00:00Z", "2023-03-01T19:00:00Z", people=["StaleFriend"]),
        _event(4, "B", "2026-01-01T17:00:00Z", "2026-01-01T19:00:00Z", people=["LessStaleFriend"]),
        _event(5, "B", "2026-02-01T17:00:00Z", "2026-02-01T19:00:00Z", people=["LessStaleFriend"]),
        _event(6, "B", "2026-03-01T17:00:00Z", "2026-03-01T19:00:00Z", people=["LessStaleFriend"]),
    ]
    df = ingest.load_events_from_records(events)
    assert future_events._most_overdue_person(df, exclude=[]) == "StaleFriend"


def test_most_overdue_person_ignores_a_one_off_encounter():
    """A single event shouldn't count as a real relationship worth
    nudging a reconnection for."""
    events = [
        _event(1, "A", "2023-01-01T17:00:00Z", "2023-01-01T19:00:00Z", people=["OneTimeStranger"]),
    ]
    df = ingest.load_events_from_records(events)
    assert future_events._most_overdue_person(df, exclude=[]) is None


def test_most_overdue_person_excludes_already_suggested_people():
    events = [
        _event(1, "A", "2023-01-01T17:00:00Z", "2023-01-01T19:00:00Z", people=["StaleFriend"]),
        _event(2, "A", "2023-02-01T17:00:00Z", "2023-02-01T19:00:00Z", people=["StaleFriend"]),
        _event(3, "A", "2023-03-01T17:00:00Z", "2023-03-01T19:00:00Z", people=["StaleFriend"]),
    ]
    df = ingest.load_events_from_records(events)
    assert future_events._most_overdue_person(df, exclude=["StaleFriend"]) is None


def test_suggested_people_blends_location_company_with_a_reconnection_nudge():
    """The exact ask: base suggestions on people you normally hang out
    with in that area, plus someone you haven't spent time with in a
    while."""
    events = [
        _event(1, "Dinner", "2026-09-01T19:00:00Z", "2026-09-01T21:00:00Z", people=["FairfaxFriend"], location="El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"),
        _event(2, "Dinner", "2026-09-08T19:00:00Z", "2026-09-08T21:00:00Z", people=["FairfaxFriend"], location="El Coyote, 419 N Fairfax Ave, Los Angeles, CA 90036"),
        _event(3, "Hangout", "2023-01-01T17:00:00Z", "2023-01-01T19:00:00Z", people=["StaleFriend"]),
        _event(4, "Hangout", "2023-02-01T17:00:00Z", "2023-02-01T19:00:00Z", people=["StaleFriend"]),
        _event(5, "Hangout", "2023-03-01T17:00:00Z", "2023-03-01T19:00:00Z", people=["StaleFriend"]),
    ]
    df = ingest.load_events_from_records(events)
    people, reason = future_events.suggested_people(df, category=None, location="Molly Malone's, 575 S Fairfax Ave, Los Angeles, CA 90036")
    assert "FairfaxFriend" in people
    assert "StaleFriend" in people
    assert "Fairfax" in reason
    assert "haven't seen" in reason
