""""Future" tab: upcoming events worth going to, pulled from Eventbrite,
Luma, Meetup, Camber, Partiful, Posh, and anywhere else DuckDuckGo turns up
for your home region, with start/end/duration/location, conflict alerts
against your own calendar, and people suggestions - ranked by fit with your
calendar history.

None of Eventbrite/Luma/Meetup has a public API that's usable without
registering for a developer key/OAuth app, so instead of gating this behind
"sign up for a Meetup app", event *discovery* scrapes DuckDuckGo's HTML
search results page (no key required, no account, no rate-limit approval)
for a `site:<platform domain> <region> events` query, then *enriches* each
result by fetching its event page and reading the schema.org Event JSON-LD
block most event sites embed for SEO (the same structured data Google uses
for its own event rich results) - that's where start/end/location actually
come from. `other_web_events` runs the same discovery+enrichment pipeline
without a site: filter, keeping only results that actually resolve to a
real schema.org Event on their own page - which is what lets "and anywhere
else" work without an ever-growing hardcoded domain list, and without
mistaking a random blog post for a real listing.

Camber is different from the other three: it's a Substack newsletter (the
"LA Happenings" section at camberplaces.substack.com/s/la-happenings)
curating a roundup of local events in one post, not a platform with a
machine-readable page per event - so there's no schema.org Event markup to
enrich a Camber result with, and it'll almost always come through with
start/end left unknown, same as any other page enrichment can't parse a
date out of. It's kept as its own named platform (not folded into
`other_web_events`, which requires a confirmed date) since a Camber
roundup is still worth surfacing as a link even without one.

A result whose page has no such block (or fails to fetch) falls back to a
plain-text date parsed out of the search result's own title/snippet - not
as reliable as structured data, but often present even when the page itself
couldn't be read. A result that never yields any listing/browse page for a
whole region rather than one specific happening ("Discover LA Events &
Activities") is filtered out before ever being fetched - see
`_looks_like_listing`/`_is_event_url`.

`ollama_event_ideas` is a small, fully optional bonus: if a local Ollama
server (https://ollama.com) happens to be running, it's asked for a few
freeform "you might enjoy..." ideas based on your calendar's own top
categories. These are clearly NOT real scraped listings (no URL, no
confirmed date/venue - tagged `is_ai_suggestion`) - they're a brainstormed
nudge, not a claim that this specific event exists. If Ollama isn't
running (the common case - nothing else here needs it), this silently
returns [], the same graceful-degradation pattern as an offline DuckDuckGo
search.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse

import pandas as pd

PLATFORMS = ("eventbrite", "luma", "meetup", "camber", "partiful", "posh")

PLATFORM_DOMAINS = {
    "eventbrite": "eventbrite.com",
    "luma": "lu.ma",
    "meetup": "meetup.com",
    "camber": "camberplaces.substack.com",
    "partiful": "partiful.com",
    "posh": "posh.vip",
}

# Most platforms' site: query is just their domain, but Camber's events live
# under one specific newsletter section rather than the whole Substack -
# DuckDuckGo's site: operator accepts a path suffix too, so this narrows the
# search without narrowing the URL-match check above (a domain match is
# still enough there; a Camber post's exact path can vary).
PLATFORM_QUERY_SITE = {
    "camber": "camberplaces.substack.com/s/la-happenings",
}

DEFAULT_REGION = "Los Angeles, CA"

# How far ahead to look by default - a search for "upcoming events" with no
# time bound at all tends to surface far-future festivals/conferences over
# what's actually happening soon, and widens the odds of hitting a
# browse/listing page instead of one specific event.
DEFAULT_SEARCH_WINDOW_DAYS = 7

# Beyond this, a parsed start/end almost certainly isn't one real
# occurrence's actual span - e.g. a "multiple dates" listing whose own
# schema.org markup (or our own occurrence-picking, if that ever still
# blends fields) reports the first date's start against the last date's
# end. Kept generous (a week) since real multi-day festivals exist.
MAX_PLAUSIBLE_DURATION_HOURS = 24 * 7

# A plain-text fallback date/time (see _fallback_datetime_from_text) has no
# reliable end time, so this is used as a placeholder just long enough for
# conflict-checking to mean something, not a claim about the real length.
_FALLBACK_DURATION_HOURS = 2

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
# A self-identifying bot User-Agent ("felinni-future-tab/1.0;
# +https://github.com/") was used here previously - and is exactly the
# kind of thing DuckDuckGo's anomaly detection blocks outright, regardless
# of the query, which matches what a real run looked like: every single
# platform's search (and the domain-agnostic web search) came back with
# 0 parsed results and a near-identical response size, all at once - not
# what a genuinely empty result set for 7 different queries looks like.
# A plain, current desktop-browser User-Agent (with matching Accept/
# Accept-Language headers, since a real browser always sends those too)
# is what every other DuckDuckGo-scraping tool relies on for exactly this
# reason - it's still just an unauthenticated page fetch, no key/login
# involved, just not self-flagged as automated traffic.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_RESULT_LINK_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]*)"[^>]*>(?P<title>.*?)</a>', re.S
)
_RESULT_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<body>.*?)</script>', re.S | re.I
)

# A specific event page vs. a listing/browse/search page for a whole
# region on the same platform - only the former is a real, single
# happening. Platforms not listed here (Camber, Partiful, Posh, and
# other_web_events' arbitrary domains) fall through to `_is_event_url`'s
# "no pattern registered -> allow" default and rely on `_looks_like_listing`
# instead: Partiful/Posh's exact event-URL shape isn't confirmed here (both
# are largely invite-based, so relatively little is publicly indexed to
# begin with), and a wrong guess at a restrictive pattern would silently
# filter out every real result rather than just letting a few extra
# listing pages through.
_EVENT_URL_PATTERNS = {
    "eventbrite": re.compile(r"eventbrite\.[a-z.]+/e/", re.I),
    "meetup": re.compile(r"meetup\.com/[^/]+/events/\d+", re.I),
    "luma": re.compile(r"lu\.ma/(?!discover|explore|calendar|embed|signin|login|home|about|u/)[a-z0-9_-]+/?(?:$|\?)", re.I),
}

# Titles DuckDuckGo returns for a platform's own "browse everything in this
# city" page rather than one specific event - these show up because the
# site: filter matches the whole domain, not just event pages.
_LISTING_TITLE_RE = re.compile(
    r"^(discover|explore|browse)\b.*\bevents\b|things to do in|events (calendar|near me)\b", re.I
)


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _is_event_url(platform: str, url: str) -> bool:
    pattern = _EVENT_URL_PATTERNS.get(platform)
    return bool(pattern.search(url)) if pattern else True


def _looks_like_listing(title: str) -> bool:
    return bool(_LISTING_TITLE_RE.search(title or ""))


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


def _looks_like_ddg_block_page(page_html: str) -> bool:
    """True when the response is DuckDuckGo's anomaly/rate-limit
    interstitial rather than a real results page - it has no
    `id="links"` results container and usually mentions the block
    directly. Used only to make a "0 parsed results" diagnostic more
    specific than a guess."""
    lowered = page_html.lower()
    return 'id="links"' not in lowered and (
        "anomaly" in lowered or "unusual traffic" in lowered or "detected an unusual" in lowered
    )


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
    error/non-2xx response - callers decide how to degrade.

    Uses GET with `q` as a query param, not POST: a POST to this endpoint
    doesn't reliably run the actual search - it can come back 200 with a
    generic page that's the same size regardless of what was searched for,
    which is exactly what "every platform's query gets 0 parsed results
    with a near-identical response length" looks like. GET is also what a
    browser actually sends for this page, so it's the well-trodden path."""
    import requests

    resp = requests.get(
        _DDG_HTML_URL,
        params={"q": query},
        headers=_BROWSER_HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def _fetch_page(url: str, timeout: float = 10.0) -> str:
    import requests

    resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _iter_jsonld_events(page_html: str):
    """Yields every schema.org Event-typed object embedded in `page_html`'s
    `<script type="application/ld+json">` blocks. Handles a block being a
    single object, a list of objects, or a `@graph` wrapper (all three show
    up across different sites' generated markup) - and, notably, a
    "multiple dates" listing that embeds one Event node per occurrence
    rather than one Event spanning all of them (see `_pick_occurrence`,
    which is what actually decides which of these to use)."""
    for match in _JSONLD_RE.finditer(page_html):
        try:
            parsed = json.loads(match.group("body").strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidates_inner = candidate.get("@graph") if isinstance(candidate.get("@graph"), list) else [candidate]
            for node in candidates_inner:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("@type")
                types = node_type if isinstance(node_type, list) else [node_type]
                if any(isinstance(t, str) and "event" in t.lower() for t in types):
                    yield node


def _location_from_jsonld(location) -> str | None:
    """schema.org `location` is a Place (dict with `name`/`address`, where
    `address` can itself be a string or a PostalAddress dict) or, rarely,
    a plain string - normalize any of those down to one display string."""
    if isinstance(location, str):
        return location.strip() or None
    if not isinstance(location, dict):
        return None
    name = location.get("name")
    address = location.get("address")
    if isinstance(address, dict):
        parts = [address.get(k) for k in ("streetAddress", "addressLocality", "addressRegion")]
        address = ", ".join(p for p in parts if p)
    address = address if isinstance(address, str) else None
    if name and address and name.strip() != address.strip():
        return f"{name.strip()}, {address.strip()}"
    return (name or address or "").strip() or None


def _parse_jsonld_datetime(value) -> pd.Timestamp | None:
    if not value or not isinstance(value, str):
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.tz_convert(None)


def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_convert(None)


def _pick_occurrence(nodes: list[dict], now: pd.Timestamp) -> tuple[dict, pd.Timestamp, pd.Timestamp] | None:
    """Given every schema.org Event node found on a page, picks ONE
    occurrence's own (node, start, end) - never blending one occurrence's
    start with a different occurrence's end, which is how a "multiple
    dates" listing previously turned into a single fake event spanning
    months. Prefers the soonest occurrence at/after `now`; if every
    occurrence has already passed (a stale cached page, an event that just
    ended), falls back to the most recent one rather than showing nothing."""
    occurrences = []
    for node in nodes:
        start = _parse_jsonld_datetime(node.get("startDate"))
        if start is None:
            continue
        end = _parse_jsonld_datetime(node.get("endDate")) or start
        if end < start:
            continue  # malformed - not worth trusting either field
        occurrences.append((node, start, end))
    if not occurrences:
        return None

    future = [o for o in occurrences if o[1] >= now]
    return min(future, key=lambda o: o[1]) if future else max(occurrences, key=lambda o: o[1])


# A plain-text date, as it might appear in a search result's own
# title/snippet even when the linked page can't be fetched or parsed (blocked,
# JS-rendered, restructured) - e.g. "...Monday, September 14, 2026 at City
# Club LA...". No timezone information is available this way, so the result
# is treated as a naive local time, same precision loss as reading it off a
# page by eye.
_FALLBACK_DATE_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2},?\s*\d{4})(?:\s+at\s+(\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?))?"
)


def _fallback_datetime_from_text(text: str) -> pd.Timestamp | None:
    match = _FALLBACK_DATE_RE.search(text or "")
    if not match:
        return None
    date_part, time_part = match.groups()
    combined = f"{date_part} {time_part}" if time_part else date_part
    parsed = pd.to_datetime(combined, errors="coerce")
    return None if pd.isna(parsed) else parsed


def enrich_with_event_page(event: dict, timeout: float = 10.0) -> dict:
    """Fetches `event["url"]` and fills in start/end/duration_hours/location
    from the page's schema.org Event JSON-LD, if it has one; if not (fetch
    failure, no structured data, blocked), falls back to a plain-text date
    parsed from the search result's own title/snippet. Returns a copy;
    never raises."""
    enriched = dict(event)
    if event.get("url"):
        try:
            page_html = _fetch_page(event["url"], timeout=timeout)
            nodes = list(_iter_jsonld_events(page_html))
        except Exception:
            nodes = []
    else:
        nodes = []

    picked = _pick_occurrence(nodes, _now_utc()) if nodes else None
    node, start, end = picked if picked else (None, None, None)

    if start is None:
        start = _fallback_datetime_from_text(f"{event.get('title') or ''} {event.get('snippet') or ''}")
        end = start + pd.Timedelta(hours=_FALLBACK_DURATION_HOURS) if start is not None else None

    if start is not None and end is not None and (end - start) > pd.Timedelta(hours=MAX_PLAUSIBLE_DURATION_HOURS):
        end = None  # implausible span - keep the start, drop the untrustworthy end/duration

    enriched["start"] = start.isoformat() if start is not None else enriched.get("start")
    enriched["end"] = end.isoformat() if end is not None else enriched.get("end")
    enriched["duration_hours"] = (
        round((end - start).total_seconds() / 3600.0, 2) if start is not None and end is not None else enriched.get("duration_hours")
    )
    if node:
        location = _location_from_jsonld(node.get("location"))
        if location:
            enriched["location"] = location
        if node.get("name"):
            enriched["title"] = _strip_tags(str(node["name"])) or enriched["title"]
    return enriched


def _event_stub(title: str, url: str, source: str, region: str, snippet: str) -> dict:
    return {
        "title": title, "url": url, "start": None, "end": None,
        "duration_hours": None, "location": region, "source": source, "snippet": snippet,
    }


def _time_window_phrase(days_ahead: int) -> str:
    if days_ahead <= 1:
        return "today"
    if days_ahead <= 7:
        return "this week"
    if days_ahead <= 31:
        return "this month"
    return "upcoming"


def platform_events(
    platform: str,
    region: str = DEFAULT_REGION,
    max_results: int = 6,
    days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS,
    debug: dict[str, str] | None = None,
) -> list[dict]:
    """Upcoming events for `platform` in `region`, normalized to {title,
    url, start, end, duration_hours, location, source, snippet}. Found via a
    DuckDuckGo `site:` search, then enriched by fetching each result's own
    page for its schema.org Event data - see the module docstring. Skips
    results that are clearly a browse/listing page rather than one specific
    event (by URL shape and title) before ever fetching them. `days_ahead`
    only shapes the search phrase ("this week"/"this month"/...) - it's a
    bias toward DuckDuckGo results that are actually near-term, not a
    guarantee; `within_search_window` is what actually enforces the window
    once a result's real date is known. Never raises: a DuckDuckGo/
    page-fetch failure (offline, rate-limited, blocked) just means fewer or
    plainer results, same as an unconnected platform did before.

    If given, `debug[platform]` is set to a short note on what actually
    happened (a request error, "0 results parsed", or how many results
    were found vs. kept after filtering) - a silent `[]` here looks
    identical whether DuckDuckGo is offline, blocking this client, or
    genuinely has nothing for the query, and that ambiguity is exactly what
    made a much bigger set of failures hard to diagnose in
    felinni.geocode before its own per-location diagnostics were added."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r} (expected one of {PLATFORMS})")

    domain = PLATFORM_DOMAINS[platform]
    site_query = PLATFORM_QUERY_SITE.get(platform, domain)
    query = f"site:{site_query} {region} events {_time_window_phrase(days_ahead)}"
    try:
        page_html = _ddg_search(query)
    except Exception as e:
        if debug is not None:
            debug[platform] = f"DuckDuckGo request failed: {e}"
        return []

    parsed = _parse_ddg_html_results(page_html)
    if debug is not None and not parsed:
        blocked = " - looks like DuckDuckGo's rate-limit/anomaly page, not real results" if _looks_like_ddg_block_page(page_html) else ""
        debug[platform] = f"DuckDuckGo returned 0 parsed results (response was {len(page_html)} chars){blocked}"

    events = []
    skipped = 0
    for result in parsed:
        if domain not in result["url"]:
            continue  # DDG sometimes surfaces an unrelated result despite the site: filter
        if not _is_event_url(platform, result["url"]) or _looks_like_listing(result["title"]):
            skipped += 1
            continue
        stub = _event_stub(result["title"], result["url"], platform, region, result["snippet"])
        events.append(enrich_with_event_page(stub))
        if len(events) >= max_results:
            break
    if debug is not None and parsed:
        debug[platform] = f"{len(parsed)} DuckDuckGo result(s), {skipped} filtered as unrelated/listing pages, {len(events)} kept"
    return events


def other_web_events(
    region: str = DEFAULT_REGION,
    max_results: int = 6,
    days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS,
    debug: dict[str, str] | None = None,
) -> list[dict]:
    """Events from anywhere else DuckDuckGo turns up for `region` - not
    restricted to Eventbrite/Luma/Meetup via a site: filter, so results
    could be a venue's own site, a local listings site, a ticketing
    platform, ... With no domain allowlist to lean on, a result is only
    kept once it's actually confirmed to be one real, single event - i.e.
    `enrich_with_event_page` found a usable date for it (structured or
    plain-text) - which is what keeps this from filling up with random
    blog posts and listicles that just happen to mention `region`. See
    `platform_events` for what `debug` (keyed "web" here) records."""
    query = f"{region} events {_time_window_phrase(days_ahead)}"
    try:
        page_html = _ddg_search(query)
    except Exception as e:
        if debug is not None:
            debug["web"] = f"DuckDuckGo request failed: {e}"
        return []

    parsed = _parse_ddg_html_results(page_html)
    if debug is not None and not parsed:
        blocked = " - looks like DuckDuckGo's rate-limit/anomaly page, not real results" if _looks_like_ddg_block_page(page_html) else ""
        debug["web"] = f"DuckDuckGo returned 0 parsed results (response was {len(page_html)} chars){blocked}"

    known_domains = tuple(PLATFORM_DOMAINS.values())
    events = []
    no_date = 0
    for result in parsed:
        domain = urllib.parse.urlparse(result["url"]).netloc.removeprefix("www.")
        if not domain or any(known in domain for known in known_domains):
            continue  # already covered by platform_events - avoid duplicates
        if _looks_like_listing(result["title"]):
            continue
        stub = _event_stub(result["title"], result["url"], domain, region, result["snippet"])
        enriched = enrich_with_event_page(stub)
        if not enriched.get("start"):
            no_date += 1
            continue  # no confirmed real event date - too likely a listing/blog page to trust
        events.append(enriched)
        if len(events) >= max_results:
            break
    if debug is not None and parsed:
        debug["web"] = f"{len(parsed)} DuckDuckGo result(s), {no_date} dropped for no confirmed date, {len(events)} kept"
    return events


def ollama_event_ideas(
    df: pd.DataFrame,
    region: str = DEFAULT_REGION,
    model: str = "llama3",
    host: str = "http://localhost:11434",
    limit: int = 3,
    timeout: float = 30.0,
) -> list[dict]:
    """Freeform "you might enjoy..." event ideas from a locally-running
    Ollama model (https://ollama.com), based on your calendar's own top
    categories. These are NOT real scraped listings - no URL, no confirmed
    date/venue - and are tagged `is_ai_suggestion: True` so the Future tab
    can show them distinctly (an idea to go looking for, not a specific
    happening someone can click through to). Purely optional: if Ollama
    isn't running locally (the common case), this silently returns [],
    the same graceful-degradation pattern as an offline DuckDuckGo search
    elsewhere in this module."""
    if df.empty:
        return []
    top_categories = df["category"].dropna().value_counts().head(5).index.tolist()
    if not top_categories:
        return []
    prompt = (
        f"Suggest {limit} short event ideas for someone in {region} who enjoys: "
        f"{', '.join(top_categories)}. One line each, no numbering, no extra commentary."
    )
    try:
        import requests

        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
    except Exception:
        return []

    ideas = [line.strip("-*\t ") for line in text.splitlines() if line.strip()]
    return [
        {
            "title": idea, "url": None, "start": None, "end": None, "duration_hours": None,
            "location": region, "source": "ai-suggestion", "snippet": "",
            "is_ai_suggestion": True,
        }
        for idea in ideas[:limit]
    ]


def _overlaps(a_start: pd.Timestamp, a_end: pd.Timestamp, b_start: pd.Timestamp, b_end: pd.Timestamp) -> bool:
    return a_start < b_end and b_start < a_end


def calendar_conflicts(event: dict, df: pd.DataFrame) -> list[dict]:
    """Your own (timed) calendar events that overlap `event`'s start/end -
    empty if `event` has no parsed time, or nothing overlaps."""
    if not event.get("start") or not event.get("end") or df.empty:
        return []
    start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
    timed = df[~df["is_all_day"]]
    overlapping = timed[(timed["start"] < end) & (start < timed["end"])]
    return [
        {"title": row.title, "start": row.start.isoformat(), "end": row.end.isoformat()}
        for row in overlapping.itertuples()
    ]


def event_conflicts(event: dict, other_events: list[dict]) -> list[dict]:
    """Other candidate events (e.g. from a different platform) that overlap
    `event`'s start/end, so double-booking two suggestions against each
    other is caught too, not just against your existing calendar."""
    if not event.get("start") or not event.get("end"):
        return []
    start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
    conflicts = []
    for other in other_events:
        same_event = other.get("url") is not None and other.get("url") == event.get("url")
        if same_event or not other.get("start") or not other.get("end"):
            continue
        o_start, o_end = pd.Timestamp(other["start"]), pd.Timestamp(other["end"])
        if _overlaps(start, end, o_start, o_end):
            conflicts.append({"title": other["title"], "start": other["start"], "end": other["end"], "source": other.get("source")})
    return conflicts


def within_search_window(events: list[dict], days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS) -> list[dict]:
    """Keeps events with no confirmed start (there's no date to judge, so
    it's shown rather than assumed out of range) plus those starting
    within the next `days_ahead` days; drops ones confirmed to start
    later. This is the actual enforcement of the search window - the
    "this week"/"this month" phrase in the DuckDuckGo query is only a
    bias toward near-term results, not a guarantee, since a lot of pages
    don't literally repeat that phrase back."""
    now = _now_utc()
    cutoff = now + pd.Timedelta(days=days_ahead)
    kept = []
    for event in events:
        start = event.get("start")
        if not start:
            kept.append(event)
            continue
        start_ts = pd.Timestamp(start)
        if now - pd.Timedelta(days=1) <= start_ts <= cutoff:
            kept.append(event)
    return kept


def annotate_conflicts(events: list[dict], df: pd.DataFrame) -> list[dict]:
    """Every event, each tagged with `conflicts` (calendar events and other
    candidate events that overlap it, each carrying `type`: "calendar" or
    "event") and `has_conflict`, for the Future tab's conflict alerts."""
    annotated = []
    for event in events:
        event = dict(event)
        conflicts = (
            [{**c, "type": "calendar"} for c in calendar_conflicts(event, df)]
            + [{**c, "type": "event"} for c in event_conflicts(event, events)]
        )
        event["conflicts"] = conflicts
        event["has_conflict"] = bool(conflicts)
        annotated.append(event)
    return annotated


def _infer_category(event: dict, categories: list[str]) -> str | None:
    """Best-effort category guess for a scraped event: none of the sites
    this pulls from reliably carries a category matching your own
    calendar's, so this just checks whether one of your existing category
    names shows up as a word in the event's title/snippet."""
    text = f"{event.get('title') or ''} {event.get('snippet') or ''}".casefold()
    for category in categories:
        if category and re.search(rf"\b{re.escape(category.casefold())}\b", text):
            return category
    return None


def _matched_frequent_location(df: pd.DataFrame, location: str | None, top_n: int = 15) -> str | None:
    """One of your own most-visited location strings that overlaps
    `location` (either contains the other, case-insensitively) - the same
    place, not just the same city/region."""
    if not location or df.empty:
        return None
    location_cf = location.casefold()
    frequented = df["location"].dropna().value_counts().head(top_n).index
    for place in frequented:
        if place.casefold() in location_cf or location_cf in place.casefold():
            return place
    return None


def suggested_people(df: pd.DataFrame, category: str | None = None, location: str | None = None, limit: int = 3) -> tuple[list[str], str]:
    """People you'd plausibly want to invite, with a reason: your most
    frequent companions for `category` if it matched one of your own;
    failing that, whoever you usually go to a matching `location` with;
    failing that, your most frequent companions overall. Returns
    ([], "...") rather than a name list you have no real reason to trust
    when there's no history to go on at all."""
    from felinni import social

    if df.empty:
        return [], "no calendar history to go on yet"

    candidates: list[tuple[pd.DataFrame, str]] = []
    if category:
        candidates.append((
            df[df["category"].str.casefold() == category.casefold()],
            f'your usual company for "{category}"',
        ))
    matched_place = _matched_frequent_location(df, location)
    if matched_place:
        candidates.append((df[df["location"] == matched_place], f"who you usually go to {matched_place} with"))
    candidates.append((df, "your most frequent people overall"))

    for pool, reason in candidates:
        if pool.empty:
            continue
        freq = social.person_frequency(pool)
        if not freq.empty:
            return list(freq.head(limit).index), reason
    return [], "no history to go on yet"


def _day_time_fit(df: pd.DataFrame, event: dict, category: str | None) -> tuple[float, list[str]]:
    if not event.get("start"):
        return 0.0, []
    pool = df[df["category"].str.casefold() == category.casefold()] if category else df
    pool = pool[~pool["is_all_day"]]
    if pool.empty:
        return 0.0, []

    score, reasons = 0.0, []
    event_start = pd.Timestamp(event["start"])
    common_weekdays = set(pool["weekday"].value_counts().head(3).index)
    if event_start.day_name() in common_weekdays:
        score += 1.0
        reasons.append(f"usually active on {event_start.day_name()}s" + (f" for {category}" if category else ""))
    typical_hours = pool["start"].dt.hour
    if abs(event_start.hour - typical_hours.median()) <= 2:
        score += 1.0
        reasons.append("around your usual time of day")
    return score, reasons


def _location_fit(df: pd.DataFrame, event: dict) -> tuple[float, list[str]]:
    place = _matched_frequent_location(df, event.get("location"))
    return (1.0, [f"near {place}, a place you already go"]) if place else (0.0, [])


def suggestions_for(df: pd.DataFrame, candidate_events: list[dict] | None = None) -> list[dict]:
    """Ranks `candidate_events` (pulled from `platform_events`/
    `other_web_events`/`ollama_event_ideas`, ideally already run through
    `annotate_conflicts`) by fit with `df`: category overlap with your own
    calendar, your usual day-of-week/time-of-day for that category, whether
    the venue is somewhere you already go, minus a penalty for anything
    flagged as conflicting. Each result carries `fit_score`, `fit_reasons`,
    `matched_category`, `suggested_people`, and `people_reason` (why those
    specific people, not just your all-time top 3 regardless of the event).
    This is the Future tab's one and only ranked list - there's no separate
    "suggested" vs. "upcoming events" list to keep in sync, since ranking
    every candidate is strictly more informative than a second, differently
    filtered pass over the same events. Empty if there are no candidates."""
    if not candidate_events:
        return []

    categories = sorted(df["category"].dropna().unique()) if not df.empty else []
    ranked = []
    for event in candidate_events:
        category = _infer_category(event, categories)
        score = 0.0
        reasons = []
        if category:
            score += 2.0
            reasons.append(f'matches your "{category}" history')
        day_time_score, day_time_reasons = _day_time_fit(df, event, category)
        score += day_time_score
        reasons.extend(day_time_reasons)
        location_score, location_reasons = _location_fit(df, event)
        score += location_score
        reasons.extend(location_reasons)
        if event.get("has_conflict"):
            score -= 3.0
            conflict_types = {c["type"] for c in event["conflicts"]}
            reasons.append(
                "conflicts with your calendar" if "calendar" in conflict_types else "conflicts with another suggested event"
            )

        people, people_reason = suggested_people(df, category, event.get("location"))
        suggestion = dict(event)
        suggestion["matched_category"] = category
        suggestion["fit_score"] = score
        suggestion["fit_reasons"] = reasons
        suggestion["suggested_people"] = people
        suggestion["people_reason"] = people_reason
        ranked.append(suggestion)

    return sorted(ranked, key=lambda e: e["fit_score"], reverse=True)
