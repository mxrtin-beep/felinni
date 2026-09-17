""""Future" tab: upcoming events worth going to, pulled from Eventbrite,
Luma, and Meetup for your home region. None of the three has a public API
that's usable without registering for a developer key/OAuth app, so instead
of gating this behind "sign up for a Meetup app", this scrapes DuckDuckGo's
HTML search results page (no key required, no account, no rate-limit
approval) for a `site:<platform domain> <region> events` query and keeps
whatever results actually link back to that platform.

This trades structured data (a real Meetup/Eventbrite/Luma API would hand
back a start time, price, RSVP count, ...) for "works today with zero
setup": all we get from a search snippet is a title, a URL, and a
description blurb, so `start` is always None here - there's nothing in the
result to reliably parse a date out of. Good enough to surface "here's what's
happening", not enough to sort by date; the webapp lists results as found.

`suggestions_for` (ranking candidate events by fit with your own calendar
history - category overlap, usual day/time, familiar places/people) is still
unimplemented: worth doing once `start`/`location` are reliable enough to
compare against `df`, which needs a real per-platform API rather than
search-result scraping.
"""
from __future__ import annotations

import html
import re
import urllib.parse

import pandas as pd

PLATFORMS = ("eventbrite", "luma", "meetup")

PLATFORM_DOMAINS = {
    "eventbrite": "eventbrite.com",
    "luma": "lu.ma",
    "meetup": "meetup.com",
}

DEFAULT_REGION = "Los Angeles, CA"

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = "Mozilla/5.0 (compatible; felinni-future-tab/1.0; +https://github.com/)"

_RESULT_LINK_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]*)"[^>]*>(?P<title>.*?)</a>', re.S
)
_RESULT_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _resolve_ddg_href(href: str) -> str:
    """DuckDuckGo's HTML results wrap every outbound link in a redirect
    (`//duckduckgo.com/l/?uddg=<url-encoded target>&rut=...`) - unwrap that
    back to the real URL. Left unchanged if it's already a direct link."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return urllib.parse.unquote(qs["uddg"][0])
    return href


def _parse_ddg_html_results(page_html: str) -> list[dict]:
    """Each search result as {title, url, snippet}, in the order DuckDuckGo
    returned them."""
    titles = list(_RESULT_LINK_RE.finditer(page_html))
    snippets = list(_RESULT_SNIPPET_RE.finditer(page_html))
    results = []
    for i, match in enumerate(titles):
        snippet = _strip_tags(snippets[i].group("snippet")) if i < len(snippets) else ""
        results.append({
            "title": _strip_tags(match.group("title")),
            "url": _resolve_ddg_href(match.group("href")),
            "snippet": snippet,
        })
    return results


def _ddg_search(query: str, timeout: float = 10.0) -> str:
    """Raw DuckDuckGo HTML results page for `query`. Raises on any network
    error/non-2xx response - callers decide how to degrade."""
    import requests

    resp = requests.post(
        _DDG_HTML_URL,
        data={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def platform_events(platform: str, region: str = DEFAULT_REGION, max_results: int = 8) -> list[dict]:
    """Upcoming-event-shaped search results for `platform` in `region`,
    normalized to {title, url, start, location, source, snippet}. `start` is
    always None (see module docstring - search snippets don't reliably carry
    a parseable date). Never raises: a DuckDuckGo request failure (offline,
    rate-limited, blocked) just means no results this time, same as an
    unconnected platform did before."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r} (expected one of {PLATFORMS})")

    domain = PLATFORM_DOMAINS[platform]
    query = f"site:{domain} {region} events"
    try:
        page_html = _ddg_search(query)
    except Exception:
        return []

    events = []
    for result in _parse_ddg_html_results(page_html):
        if domain not in result["url"]:
            continue  # DDG sometimes surfaces an unrelated result despite the site: filter
        events.append({
            "title": result["title"],
            "url": result["url"],
            "start": None,
            "location": region,
            "source": platform,
            "snippet": result["snippet"],
        })
        if len(events) >= max_results:
            break
    return events


def suggestions_for(df: pd.DataFrame, candidate_events: list[dict] | None = None) -> list[dict]:
    """Would rank `candidate_events` (pulled from `platform_events` for
    each connected platform) by fit with `df` - category overlap, your
    usual day-of-week/time-of-day, places you already frequent, people you
    tend to see. Always empty for now: search-result events don't carry a
    real start time/category to score against `df` with."""
    return []
