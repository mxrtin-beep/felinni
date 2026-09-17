""""Future" tab: upcoming events worth going to, pulled from Eventbrite,
Luma, and Meetup for your home region, with start/end/duration/location,
conflict alerts against your own calendar, and people suggestions - ranked
by fit with your calendar history.

None of the three platforms has a public API that's usable without
registering for a developer key/OAuth app, so instead of gating this behind
"sign up for a Meetup app", event *discovery* scrapes DuckDuckGo's HTML
search results page (no key required, no account, no rate-limit approval)
for a `site:<platform domain> <region> events` query, then *enriches* each
result by fetching its event page and reading the schema.org Event JSON-LD
block Meetup/Eventbrite/Luma all embed for SEO (the same structured data
Google uses for its own event rich results) - that's where start/end/
location actually come from. A result whose page has no such block (or
fails to fetch) just keeps start/end as None; it still shows up, minus the
scheduling-dependent features (conflicts, day/time fit).
"""
from __future__ import annotations

import html
import json
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
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<body>.*?)</script>', re.S | re.I
)


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


def _fetch_page(url: str, timeout: float = 10.0) -> str:
    import requests

    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _iter_jsonld_events(page_html: str):
    """Yields every schema.org Event-typed object embedded in `page_html`'s
    `<script type="application/ld+json">` blocks. Handles a block being a
    single object, a list of objects, or a `@graph` wrapper (all three show
    up across Meetup/Eventbrite/Luma's differently-generated pages)."""
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
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.tz_convert(None)


def enrich_with_event_page(event: dict, timeout: float = 10.0) -> dict:
    """Fetches `event["url"]` and fills in start/end/duration_hours/location
    from the page's schema.org Event JSON-LD, if it has one. Returns a copy;
    never raises - a fetch/parse failure (offline, page removed, no
    structured data on this particular listing) just leaves those fields as
    they were (None from `platform_events`)."""
    enriched = dict(event)
    try:
        page_html = _fetch_page(event["url"], timeout=timeout)
        node = next(_iter_jsonld_events(page_html), None)
    except Exception:
        node = None
    if node is None:
        return enriched

    start = _parse_jsonld_datetime(node.get("startDate"))
    end = _parse_jsonld_datetime(node.get("endDate"))
    if start is not None and end is None:
        end = start  # some listings omit an end time entirely
    enriched["start"] = start.isoformat() if start is not None else enriched.get("start")
    enriched["end"] = end.isoformat() if end is not None else enriched.get("end")
    enriched["duration_hours"] = (
        round((end - start).total_seconds() / 3600.0, 2) if start is not None and end is not None else None
    )
    location = _location_from_jsonld(node.get("location"))
    if location:
        enriched["location"] = location
    if node.get("name"):
        enriched["title"] = _strip_tags(str(node["name"])) or enriched["title"]
    return enriched


def platform_events(platform: str, region: str = DEFAULT_REGION, max_results: int = 6) -> list[dict]:
    """Upcoming events for `platform` in `region`, normalized to {title,
    url, start, end, duration_hours, location, source, snippet}. Found via a
    DuckDuckGo search, then enriched by fetching each result's own page for
    its schema.org Event data (start/end/duration/location) - see the
    module docstring. Never raises: a DuckDuckGo/page-fetch failure (offline,
    rate-limited, blocked) just means fewer or plainer results, same as an
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
        stub = {
            "title": result["title"],
            "url": result["url"],
            "start": None,
            "end": None,
            "duration_hours": None,
            "location": region,
            "source": platform,
            "snippet": result["snippet"],
        }
        events.append(enrich_with_event_page(stub))
        if len(events) >= max_results:
            break
    return events


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
        if other.get("url") == event.get("url") or not other.get("start") or not other.get("end"):
            continue
        o_start, o_end = pd.Timestamp(other["start"]), pd.Timestamp(other["end"])
        if _overlaps(start, end, o_start, o_end):
            conflicts.append({"title": other["title"], "start": other["start"], "end": other["end"], "source": other.get("source")})
    return conflicts


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
    """Best-effort category guess for a scraped event: none of Meetup/
    Eventbrite/Luma's JSON-LD reliably carries a category matching your own
    calendar's, so this just checks whether one of your existing category
    names shows up as a word in the event's title/snippet."""
    text = f"{event.get('title') or ''} {event.get('snippet') or ''}".casefold()
    for category in categories:
        if category and re.search(rf"\b{re.escape(category.casefold())}\b", text):
            return category
    return None


def suggested_people(df: pd.DataFrame, category: str | None, limit: int = 3) -> list[str]:
    """People you'd plausibly want to invite: your most frequent
    companions for `category` if it matched one of your own, else your most
    frequent companions overall."""
    from felinni import social

    if df.empty:
        return []
    pool = df[df["category"].str.casefold() == category.casefold()] if category else df
    if pool.empty:
        pool = df
    freq = social.person_frequency(pool)
    return list(freq.head(limit).index)


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


def _location_fit(df: pd.DataFrame, event: dict, top_n: int = 15) -> tuple[float, list[str]]:
    location = event.get("location")
    if not location or df.empty:
        return 0.0, []
    frequented = df["location"].dropna().value_counts().head(top_n).index
    location_cf = location.casefold()
    for place in frequented:
        if place.casefold() in location_cf or location_cf in place.casefold():
            return 1.0, ["near a place you already go"]
    return 0.0, []


def suggestions_for(df: pd.DataFrame, candidate_events: list[dict] | None = None) -> list[dict]:
    """Ranks `candidate_events` (pulled from `platform_events`, ideally
    already run through `annotate_conflicts`) by fit with `df`: category
    overlap with your own calendar, your usual day-of-week/time-of-day for
    that category, whether the venue is somewhere you already go, minus a
    penalty for anything flagged as conflicting. Each result carries
    `fit_score`, `fit_reasons`, `matched_category`, and `suggested_people`
    (your most frequent companions for that category, to consider
    inviting). Empty if there are no candidates to rank."""
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

        suggestion = dict(event)
        suggestion["matched_category"] = category
        suggestion["fit_score"] = score
        suggestion["fit_reasons"] = reasons
        suggestion["suggested_people"] = suggested_people(df, category)
        ranked.append(suggestion)

    return sorted(ranked, key=lambda e: e["fit_score"], reverse=True)
