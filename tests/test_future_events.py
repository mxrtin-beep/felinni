"""Tests for felinni.future_events: parsing/filtering DuckDuckGo HTML search
results into the Future tab's event shape. Network calls are always mocked
via requests.post (see felinni.calendar_sources's tests for the same
pattern) - these never hit the real DuckDuckGo endpoint."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

from felinni import future_events

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


def _mock_response(text):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_platform_events_rejects_unknown_platform():
    with pytest.raises(ValueError):
        future_events.platform_events("carrier-pigeon")


def test_platform_events_parses_and_filters_by_domain():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)):
        events = future_events.platform_events("meetup", region="Los Angeles, CA")

    assert len(events) == 2  # the some-blog.com result is dropped
    assert events[0]["title"] == "LA Hikers & Outdoors Meetup"
    assert events[0]["url"] == "https://www.meetup.com/la-hikers/events/123/"
    assert events[0]["source"] == "meetup"
    assert events[0]["location"] == "Los Angeles, CA"
    assert events[0]["start"] is None
    assert "Griffith Park" in events[0]["snippet"]


def test_platform_events_unwraps_ddg_redirect_links():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)):
        events = future_events.platform_events("meetup", region="Los Angeles, CA")

    redirected = next(e for e in events if "la-coders" in e["url"])
    assert redirected["url"] == "https://www.meetup.com/la-coders/events/456/"


def test_platform_events_query_includes_domain_and_region():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)) as mock_post:
        future_events.platform_events("eventbrite", region="Austin, TX")

    query = mock_post.call_args.kwargs["data"]["q"]
    assert "site:eventbrite.com" in query
    assert "Austin, TX" in query


def test_platform_events_returns_empty_list_on_network_error():
    with patch("requests.post", side_effect=OSError("network unreachable")):
        assert future_events.platform_events("luma") == []


def test_platform_events_respects_max_results():
    with patch("requests.post", return_value=_mock_response(SAMPLE_DDG_HTML)):
        events = future_events.platform_events("meetup", max_results=1)
    assert len(events) == 1


def test_suggestions_for_is_empty():
    assert future_events.suggestions_for(pd.DataFrame()) == []
