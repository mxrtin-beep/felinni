""""Future" tab: upcoming events worth going to, pulled from Eventbrite,
Luma, Meetup, Camber, Partiful, Posh, and anywhere else DuckDuckGo turns up
for your home region, with start/end/duration/location, conflict alerts
against your own calendar, and people suggestions - ranked by fit with your
calendar history.

None of Eventbrite/Luma/Meetup has a public API that's usable without
registering for a developer key/OAuth app, so instead of gating this behind
"sign up for a Meetup app", event *discovery* runs a `site:<platform
domain> <region> events` query through the `ddgs` package (no key
required, no account) for a `site:<platform domain> <region> events`
query, then *enriches* each result by fetching its event page and reading
the schema.org Event JSON-LD block most event sites embed for SEO (the
same structured data Google uses for its own event rich results) - that's
where start/end/location actually come from. `other_web_events` runs the
same discovery+enrichment pipeline without a site: filter, keeping only
results that actually resolve to a real schema.org Event on their own
page - which is what lets "and anywhere else" work without an
ever-growing hardcoded domain list, and without mistaking a random blog
post for a real listing.

Discovery used to hand-roll a POST/GET to html.duckduckgo.com and
regex-parse the result page. That broke outright: DuckDuckGo started
returning an HTTP 202 "please verify" challenge page (a JS-check
interstitial, not real results) to every request from a plain `requests`
client, regardless of query, HTTP method, or User-Agent header - because
none of those change the one thing that actually gave it away, a
`requests`/urllib3 TLS handshake looks nothing like a real browser's at
the network level, independent of any header content. `ddgs` uses
`primp`, an HTTP client built specifically to reproduce a real browser's
TLS/HTTP2 fingerprint, and queries a fixed set of backend search engines
(_SEARCH_BACKENDS below - Google, Bing, Brave, Mojeek; see the comment
there for why it's exactly those four and not more) with automatic
fallback if one is unreachable or rate-limits it. Both of those
are exactly what a hand-rolled `requests` scraper of one single endpoint
can't do without reimplementing a maintained project's worth of
cat-and-mouse upkeep.

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
Activities", "Thousand Oaks Events, Tickets & Things to Do | Eventbrite")
is filtered out before ever being fetched - see
`_looks_like_listing`/`_is_event_url`/`_is_bare_domain_root` (the last one
catches a `site:` search's top "result" sometimes being the platform's own
homepage, which has no per-platform URL pattern to catch it for Camber/
Partiful/Posh - those default to allowing any URL on the domain).

The same real event is often independently listed on more than one
aggregator site (allevents.in, stayhappening.com, ... - observed
directly: identical title/address, found separately by `other_web_events`
on each domain) - `dedupe_events` collapses these before ranking, so the
Future tab doesn't show the same event twice under two different sources.

A plain-text date guess (`_fallback_datetime_from_text`) handles both
"Monday, September 14, 2026 at 7:00PM" (full month/day, year optional)
and the far more common "Sat 9/5 at 3pm" / "Thu, May 7 at 9:00 PM" shape
(weekday + numeric-or-month date + time, no year) - the original pattern
only matched the first shape, so most real event snippets came through
as "date/time unknown" even with a perfectly readable date sitting right
in the text. `other_web_events` only trusts this guess when it also comes
with `date_confirmed` (a real schema.org date, not just a text guess) -
an arbitrary, untrusted domain is far more likely to have some unrelated
date-shaped text on the page than an event platform is. The month/day
half of the pattern only recognizes real month names (matching any
capitalized word followed by a number matched things like "Substack 9"
in a page title ending "... - Substack" glued onto a snippet starting
"9/15: ...", parsing "Substack" as if it were a month).

Only a *confirmed* specific venue (`location_confirmed`, set only from
real schema.org data) is matched against your own visit history for the
"near <place>, a place you already go" fit bonus and the "consider
inviting" reason - an event whose location is just the generic search
region (Camber's roundups, or anything without real structured location
data) would otherwise substring-match almost any of your own addresses
that happen to mention the same city, producing a "near <your specific
address>" claim with no real connection to where the event actually is.

`platform_events`/`other_web_events` both print a per-reason skip
breakdown for every call (wrong domain, bare homepage, wrong URL shape,
listing title, or - for `other_web_events` - no confirmed date) - a bare
"N results found, 0 kept" doesn't say whether that's a real bug in the
filtering or the search genuinely surfacing nothing but browse pages for
that particular query. `_ddg_text_search` also prints on a raised
exception, not just on success - a search that failed for one platform's
query used to leave no visible trace of why in the terminal at all (it
would print the query, then nothing, since the exception skipped past
the success print entirely). The Flask route (`webapp.server.future_view`)
separately prints the raw/deduped/windowed/final counts of its own
merge+rank pipeline - a healthy per-source count can still collapse to
almost nothing downstream (deduped away, or windowed out), and that print
is what actually shows which stage it happened at.

`_matched_frequent_location` doesn't require an exact venue match - a
brand-new place you've never been to still counts as "somewhere you go"
if it shares a meaningful street-name word with somewhere in your history
(`_location_keywords` - "Fairfax" for two different addresses both on
Fairfax Ave), not just when the address strings literally contain one
another.

`suggested_people`/`_matched_frequent_location` try your last
`_RECENT_HISTORY_DAYS` days of history before falling back to all-time -
a person or place you saw constantly a while back but haven't since
shouldn't keep outranking who/where you're actually spending time with
now.

`ollama_event_ideas` is a small, fully optional bonus: if a local Ollama
server (https://ollama.com) happens to be running, it's asked for a few
freeform "you might enjoy..." ideas based on your calendar's own top
categories. These are clearly NOT real scraped listings (no URL, no
confirmed date/venue - tagged `is_ai_suggestion`) - they're a brainstormed
nudge, not a claim that this specific event exists. If Ollama isn't
running (the common case - nothing else here needs it), this silently
returns [], the same graceful-degradation pattern as an offline search.
"""
from __future__ import annotations

import html
import json
import re
import time
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

# A plain, current desktop-browser User-Agent for `_fetch_page` (enriching
# an already-found event page) - this is a normal single-page fetch, not
# search discovery, so it's never hit the blocking issue discovery did,
# but it's kept realistic rather than a self-identifying bot string for
# the same reason discovery's used to cause trouble.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# How many raw search results to pull per query before filtering - a
# generous multiple of `max_results` since `_is_event_url`/
# `_looks_like_listing` (platform_events) and "no confirmed date"
# (other_web_events) both drop a chunk of what comes back.
_SEARCH_OVERFETCH_FACTOR = 4

_DDG_SEARCH_TIMEOUT_SECONDS = 10.0

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
    # Confirmed directly: Partiful's own account/group landing page ("Los
    # Angeles Fun Events - Partiful", a description of the kind of events
    # a host runs, not one) slipped through with no pattern registered
    # here to catch it - real Partiful event pages are at partiful.com/e/.
    "partiful": re.compile(r"partiful\.com/e/", re.I),
}

# Titles a search turns up for a platform's own "browse everything in this
# city" page rather than one specific event - these show up because the
# site: filter matches the whole domain, not just event pages. Broadened
# beyond the original "Discover ... Events" phrasing after real listing
# pages turned up titled things like "Thousand Oaks Events, Tickets &
# Things to Do | Eventbrite" and "All Upcoming events in Thousand Oaks" -
# neither started with discover/explore/browse, so the original pattern
# let them straight through as if they were one specific event.
_LISTING_TITLE_RE = re.compile(
    r"^(discover|explore|browse|popular)\b.*\bevents\b"
    r"|things to do in|events (calendar|near me)\b"
    r"|\bevents?,?\s+(tickets|things to do)\b"
    # "all/upcoming events in <place>" - a whole-region roundup title, not
    # a bare "events in" (too broad: real single-event titles legitimately
    # say things like "Speed Dating Event in Los Angeles", which isn't a
    # listing page at all and was getting wrongly caught here).
    r"|\b(?:all\s+)?upcoming events\b|\ball\s+events?\s+in\b|\bwhat'?s on\b|\bevents calendar\b"
    # A page whose own indexed title is generic social-profile boilerplate
    # ("Luma (@luma_hq) - Instagram photos and videos") rather than
    # anything about a specific event - confirmed directly: a lu.ma page
    # with a slug that isn't in the excluded home/about/... list can still
    # be an account or tag landing page rather than one event, and some
    # sites fall back to their Instagram handle's own title metadata when
    # the page itself never set a real one. Requires the actual platform
    # name alongside the "(@handle)" bit, not just a bare "(@handle)" -
    # that alone is too broad and matches a real event's own promotional
    # handle tag ("Live Show ft. DJ Snake (@djsnake)").
    r"|instagram photos and videos|\(@[\w.]+\)\s*[•·-]\s*(?:instagram|tiktok|twitter|x)\b",
    re.I,
)


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _strip_platform_suffix(title: str, platform: str) -> str:
    """Drops a trailing "- Partiful"/"- Posh" branding suffix a search
    result's own page-title tag adds - the source is already shown
    separately as its own badge next to the title, so keeping it in the
    title too just doubles it up ("Los Angeles Fun Events - Partiful"
    next to a "partiful" badge, observed directly)."""
    domain = PLATFORM_DOMAINS.get(platform, "")
    site_name = domain.split(".")[0] if domain else ""
    names = {n for n in (platform, site_name) if n}
    if not names:
        return title
    pattern = "|".join(re.escape(n) for n in names)
    return re.sub(rf"\s*[-|•]\s*(?:{pattern})\s*$", "", title, flags=re.I).strip()


def _clean_snippet(snippet: str) -> str:
    """Light cleanup on a search result's own snippet - collapses
    whitespace, tames repeated punctuation ("FOR FREE ENTRANCE!!!" ->
    "FOR FREE ENTRANCE!"), and caps length. It's someone else's raw
    scraped text, not something worth heavily rewriting, but this keeps
    the worst of it from cluttering the card."""
    cleaned = re.sub(r"\s+", " ", snippet or "").strip()
    cleaned = re.sub(r"([!?])\1+", r"\1", cleaned)
    if len(cleaned) > 220:
        cleaned = cleaned[:220].rsplit(" ", 1)[0] + "…"
    return cleaned


def _is_event_url(platform: str, url: str) -> bool:
    pattern = _EVENT_URL_PATTERNS.get(platform)
    return bool(pattern.search(url)) if pattern else True


def _is_bare_domain_root(url: str) -> bool:
    """True for a domain's own homepage ("https://www.eventbrite.com/",
    "https://camberplaces.substack.com") - never one specific event,
    whatever the platform. Needed on top of `_is_event_url`'s per-platform
    patterns because a platform with no pattern registered (Camber,
    Partiful, Posh) defaults to allowing any URL on its domain, which let
    the bare homepage itself through as if it were an event whenever a
    `site:` search's top "result" was just the site itself."""
    path = urllib.parse.urlparse(url).path
    return path in ("", "/")


def _looks_like_listing(title: str) -> bool:
    return bool(_LISTING_TITLE_RE.search(title or ""))


# `ddgs`'s own "auto" backend mode picks from ALL registered text engines
# - bing, brave, duckduckgo, google, mojeek, yahoo, yandex, wikipedia,
# grokipedia - in a freshly SHUFFLED order every single call, and (per its
# own source) always puts wikipedia and grokipedia FIRST regardless of
# that shuffle. Neither of those two supports a `site:`-scoped web search
# at all - they're an encyclopedia and an AI-knowledge search - so "auto"
# mode wastes a chunk of its limited per-call worker budget on engines
# that can never satisfy this module's queries, then only tries a random
# handful of the general web engines after that. Passing an explicit
# backend list (as opposed to "auto"/"all") skips that shuffle entirely -
# `ddgs.DDGS._get_engines` uses the list as given, in `engine.priority`
# order (every general web engine here defaults to the same priority, so
# in practice that's just this list's own order).
#
# What an explicit list doesn't skip: `ddgs` also de-dupes by each
# engine's `provider` before ever issuing a request, and several of its
# text engines share one - reading ddgs 9.16.0's own source
# (site-packages/ddgs/engines/*.py), "yahoo" and "duckduckgo" both set
# `provider = "bing"` (both are Bing-backed under the hood) and
# "startpage" sets `provider = "google"`. Listing "yahoo" alongside
# "bing" bought nothing: whichever of the two came first in this list
# always won, and the other was silently skipped every single call
# (confirmed directly by tracing `DDGS._search_sync`) - it was never
# actually adding a real fifth backend. "yandex", the one other
# distinct-provider general web engine ddgs registers, ships with
# `disabled = True` in this version and is dropped at registration time
# regardless of what's requested. google/bing/brave/mojeek are the only
# four genuinely independent general-web backends this ddgs version has
# to offer - a `site:`-scoped run-to-run difference in results is far
# more likely to be one of those four transiently rate-limiting or
# blocking a scrape (an ongoing arms race on their end, not something
# this module's own backend selection controls) than any client-side
# randomization once "auto" mode is off.
_SEARCH_BACKENDS = "google,bing,brave,mojeek"


def _ddg_text_search(query: str, max_results: int, timeout: float = _DDG_SEARCH_TIMEOUT_SECONDS) -> list[dict]:
    """Runs `query` through `ddgs.DDGS().text(...)`, restricted to
    `_SEARCH_BACKENDS` - see the module docstring for why this replaced a
    hand-rolled scrape of html.duckduckgo.com, and the comment above
    `_SEARCH_BACKENDS` for why "auto" mode isn't used as-is. Returns each
    result as {title, url, snippet}. Raises on total failure (every
    backend engine blocked/unreachable) - callers decide how to degrade,
    same as the previous direct-HTTP approach. Prints the query and
    result count either way, so search activity is visible in the
    terminal this runs from rather than silent regardless of outcome.

    Retries once after a short pause on any failure, including ddgs's own
    "No results found." (raised when every one of `_SEARCH_BACKENDS`
    failed or genuinely returned nothing for that batch) - observed
    directly to be transient: the exact same query that raised it one run
    returned real results (and had, minutes earlier in the same session)
    on the next, which points at a passing rate-limit/timeout hiccup
    across the backends tried that round rather than a real, stable "no
    results exist" - the kind of thing a second attempt a moment later
    routinely clears."""
    from ddgs import DDGS

    print(f"[future_events] search: {query!r}", flush=True)
    last_error: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(1.0)
            print(f"[future_events] search: {query!r} (retry)", flush=True)
        try:
            results = DDGS(timeout=timeout).text(query, max_results=max_results, backend=_SEARCH_BACKENDS)
        except Exception as e:
            last_error = e
            print(f"[future_events] search failed: {e}", flush=True)
            continue
        print(f"[future_events] search returned {len(results)} result(s)", flush=True)
        break
    else:
        raise last_error
    return [
        {"title": r.get("title") or "", "url": r.get("href") or "", "snippet": r.get("body") or ""}
        for r in results
        if r.get("href")
    ]


def _fetch_page(url: str, timeout: float = 10.0) -> str:
    import requests

    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
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


def _clean_address_text(text: str) -> str:
    """Collapses exact-duplicate comma-separated segments in a freeform
    address string. Some event aggregators (allevents.in and others,
    observed directly) embed the same address two or three times over in
    one schema.org field, each copy formatted slightly differently -
    "1321 E Thousand Oaks Blvd. #108, Thousand Oaks, CA, United States,
    California 91362, 1321 E Thousand Oaks Blvd, Thousand Oaks, CA
    91362-2821, United States, Thousand Oaks, CA" - rather than one clean
    address. Keeps the first occurrence of each distinct segment
    (case-insensitively), in order; doesn't catch near-duplicates that
    differ by more than casing/whitespace (e.g. "Blvd." vs "Blvd"), but
    removing the exact repeats alone makes a real difference."""
    seen = set()
    parts = []
    for part in text.split(","):
        stripped = part.strip()
        key = stripped.casefold()
        if not stripped or key in seen:
            continue
        seen.add(key)
        parts.append(stripped)
    return ", ".join(parts)


def _location_from_jsonld(location) -> str | None:
    """schema.org `location` is a Place (dict with `name`/`address`, where
    `address` can itself be a string or a PostalAddress dict) or, rarely,
    a plain string - normalize any of those down to one display string."""
    if isinstance(location, str):
        cleaned = _clean_address_text(location.strip())
        return cleaned or None
    if not isinstance(location, dict):
        return None
    name = location.get("name")
    address = location.get("address")
    if isinstance(address, dict):
        parts = [address.get(k) for k in ("streetAddress", "addressLocality", "addressRegion")]
        address = ", ".join(p for p in parts if p)
    address = address if isinstance(address, str) else None
    if address:
        address = _clean_address_text(address)
    if name and address and name.strip() != address.strip():
        return f"{name.strip()}, {address.strip()}"
    return (name or address or "").strip() or None


def _parse_jsonld_datetime(value) -> pd.Timestamp | None:
    """schema.org dates commonly carry an explicit UTC offset ("-07:00"
    for Pacific) - keep the wall-clock time the page itself specified
    (its own venue/local timezone) rather than converting to UTC. Every
    other stored start/end in this app (both here and in felinni.ingest,
    for the user's own calendar) is naive local time, and the frontend's
    `new Date(isoString)` parses a timezone-less string as local time per
    the JS spec - converting to UTC here (the previous behavior) silently
    shifted every timed result by its venue's UTC offset once the
    browser re-interpreted that already-shifted number as if it were
    already local (an 8pm Pacific show, "-07:00", displayed as 3am the
    next day)."""
    if not value or not isinstance(value, str):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.tz_localize(None)
    return parsed


def _now_local() -> pd.Timestamp:
    """Naive "now", in the same terms as everything else this module
    compares timestamps against - a page's own local/venue wall-clock
    time, not UTC (see `_parse_jsonld_datetime`)."""
    return pd.Timestamp.now()


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
# title/snippet even when the linked page can't be fetched or parsed
# (blocked, JS-rendered, restructured). Two shapes are handled, both
# confirmed directly on real event snippets:
# - "Monday, September 14, 2026 at 7:00PM" - a full month/day, with or
#   without a year and a leading weekday, both optional.
# - "Sat 9/5 at 3pm" / "Thu, May 7 at 9:00 PM" - the *far* more common
#   shape in practice (ticketing sites default to "this weekend" phrasing
#   without a year), which the original pattern didn't match at all -
#   every event snippet in that shape came through as "date/time unknown"
#   even though the date was right there in the text. When no year is
#   present, pandas' underlying dateutil parser defaults the missing
#   year to the current one; a date that's actually already passed this
#   year (an annual event's next occurrence being next year, say) still
#   gets dropped safely by `within_search_window` rather than shown with
#   a wrong date.
# No timezone information is available this way either, so the result is
# treated as a naive local time, same precision loss as reading it off a
# page by eye.
_FALLBACK_WEEKDAY_PREFIX = r"(?:Mon|Tue(?:s)?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)[a-z]*\.?,?\s+"
# An explicit month-name alternation, not "any capitalized word" - the
# latter (this pattern's first version) matched things like "Substack 9"
# in "Camber | Mady Maio - Substack 9/15: A Dolly Parton..." as if
# "Substack" were a month name, producing a bogus parsed date instead of
# reaching the real "9/15" a few words later.
_MONTH_NAMES = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_FALLBACK_MONTH_DAY = rf"(?:{_MONTH_NAMES})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?"
_FALLBACK_NUMERIC_DATE = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
_FALLBACK_TIME = r"\d{1,2}(?::\d{2})?\s*[APap]\.?[Mm]\.?"
_FALLBACK_DATE_RE = re.compile(
    rf"(?:{_FALLBACK_WEEKDAY_PREFIX})?(?P<date>{_FALLBACK_MONTH_DAY}|{_FALLBACK_NUMERIC_DATE})"
    rf"(?:\s+at\s+(?P<time>{_FALLBACK_TIME}))?"
)


def _fallback_datetime_from_text(text: str) -> pd.Timestamp | None:
    match = _FALLBACK_DATE_RE.search(text or "")
    if not match:
        return None
    date_part, time_part = match.group("date"), match.group("time")
    combined = f"{date_part} {time_part}" if time_part else date_part
    # `pd.to_datetime` defaults a missing year to 0001, not the current
    # year, for a string like "9/5 3pm" - `dateutil.parser.parse`'s own
    # `default=` is what actually fills in "this year" for whichever
    # component (just the year, here - month/day/time are always present
    # in what `_FALLBACK_DATE_RE` matched) is missing from the text.
    import dateutil.parser

    # Normalized (midnight) so a date with no time in the text still
    # defaults to midnight, same as before - not whatever moment this
    # happens to run.
    default = pd.Timestamp.now().normalize().to_pydatetime()
    try:
        parsed = dateutil.parser.parse(combined, default=default)
    except (ValueError, OverflowError):
        return None
    return pd.Timestamp(parsed)


def enrich_with_event_page(event: dict, timeout: float = 10.0) -> dict:
    """Fetches `event["url"]` and fills in start/end/duration_hours/location
    from the page's schema.org Event JSON-LD, if it has one; if not (fetch
    failure, no structured data, blocked), falls back to a plain-text date
    parsed from the search result's own title/snippet. Also sets
    `date_confirmed`: True only when the date came from real structured
    data, not a plain-text guess - `other_web_events` uses this to require
    the stronger signal for an arbitrary, untrusted domain, where a
    plain-text date guess is far more likely to be a false positive (a
    random date elsewhere on an unrelated page) than it is on a page
    that's already confirmed to be one of the known event platforms.
    Returns a copy; never raises."""
    enriched = dict(event)
    if event.get("url"):
        try:
            page_html = _fetch_page(event["url"], timeout=timeout)
            nodes = list(_iter_jsonld_events(page_html))
        except Exception:
            nodes = []
    else:
        nodes = []

    picked = _pick_occurrence(nodes, _now_local()) if nodes else None
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
    enriched["date_confirmed"] = node is not None
    if node:
        location = _location_from_jsonld(node.get("location"))
        if location:
            enriched["location"] = location
            enriched["location_confirmed"] = True
        if node.get("name"):
            enriched["title"] = _strip_tags(str(node["name"])) or enriched["title"]
    return enriched


def _event_stub(title: str, url: str, source: str, region: str, snippet: str) -> dict:
    return {
        "title": title, "url": url, "start": None, "end": None,
        "duration_hours": None, "location": region, "source": source, "snippet": snippet,
        "date_confirmed": False, "location_confirmed": False,
    }


def _time_window_phrase(days_ahead: int) -> str:
    if days_ahead <= 1:
        return "today"
    if days_ahead <= 7:
        return "this week"
    if days_ahead <= 31:
        return "this month"
    return "upcoming"


def _search_or_record_failure(
    query: str,
    max_results: int,
    debug: dict[str, str] | None,
    debug_key: str,
    fallback_query: str | None = None,
) -> list[dict] | None:
    """Shared opening step of platform_events/other_web_events (see
    platform_events' docstring for what `debug[debug_key]` diagnoses).
    Returns `None` on a search failure - the caller should bail out
    immediately - or the results list otherwise, possibly empty (the
    caller still prints its own per-source line in that case).

    If `fallback_query` is given and `query` comes back empty (not
    erroring - a real "nothing indexed for this" isn't a failure to
    retry) or fails outright, retries once with the broader query before
    giving up. `platform_events`/`other_web_events` pass their own query
    with `_time_window_phrase` appended as `query`, and the same query
    without it as `fallback_query`: that phrase is only a soft bias
    toward near-term results (the real window is enforced downstream by
    `within_search_window`, not by this phrase), and for a smaller or
    less-active region, the exact wording a search engine indexed a page
    under may just not include it - "Thousand Oaks, CA events this week"
    matching nothing is a narrower question than whether eventbrite.com
    has anything at all indexed for Thousand Oaks."""
    attempts = [query] + ([fallback_query] if fallback_query and fallback_query != query else [])
    last_error: Exception | None = None
    for attempt_query in attempts:
        try:
            parsed = _ddg_text_search(attempt_query, max_results=max_results * _SEARCH_OVERFETCH_FACTOR)
        except Exception as e:
            last_error = e
            continue
        if parsed:
            return parsed
        last_error = None  # a clean empty result, not an error - keep trying the next attempt, if any
    if last_error is not None:
        if debug is not None:
            debug[debug_key] = f"search failed: {last_error}"
        return None
    if debug is not None:
        debug[debug_key] = "search returned 0 results"
    return []


def platform_events(
    platform: str,
    region: str = DEFAULT_REGION,
    max_results: int = 6,
    days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS,
    debug: dict[str, str] | None = None,
) -> list[dict]:
    """Upcoming events for `platform` in `region`, normalized to {title,
    url, start, end, duration_hours, location, source, snippet}. Found via a
    `site:` search (see the module docstring for how), then enriched by
    fetching each result's own page for its schema.org Event data. Skips
    results that are clearly a browse/listing page rather than one specific
    event (by URL shape and title) before ever fetching them. `days_ahead`
    only shapes the search phrase ("this week"/"this month"/...) - it's a
    bias toward results that are actually near-term, not a guarantee;
    `within_search_window` is what actually enforces the window once a
    result's real date is known. Never raises: a search/page-fetch
    failure (offline, every backend blocked) just means fewer or plainer
    results, same as an unconnected platform did before.

    If given, `debug[platform]` is set to a short note on what actually
    happened (a request error, "0 results", or how many results were
    found vs. kept after filtering) - a silent `[]` here looks identical
    whether search is offline, blocked, or genuinely has nothing for the
    query, and that ambiguity is exactly what made a much bigger set of
    failures hard to diagnose in felinni.geocode before its own
    per-location diagnostics were added."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r} (expected one of {PLATFORMS})")

    domain = PLATFORM_DOMAINS[platform]
    site_query = PLATFORM_QUERY_SITE.get(platform, domain)
    query = f"site:{site_query} {region} events {_time_window_phrase(days_ahead)}"
    fallback_query = f"site:{site_query} {region} events"
    parsed = _search_or_record_failure(query, max_results, debug, platform, fallback_query=fallback_query)
    if parsed is None:
        return []

    events = []
    reasons = {"wrong_domain": 0, "bare_root": 0, "wrong_url_shape": 0, "listing_title": 0}
    sample_urls: dict[str, list[str]] = {"wrong_url_shape": [], "bare_root": [], "listing_title": []}
    for result in parsed:
        if domain not in result["url"]:
            reasons["wrong_domain"] += 1
            continue  # the search sometimes surfaces an unrelated result despite the site: filter
        if _is_bare_domain_root(result["url"]):
            reasons["bare_root"] += 1
            if len(sample_urls["bare_root"]) < 3:
                sample_urls["bare_root"].append(result["url"])
            continue
        if not _is_event_url(platform, result["url"]):
            reasons["wrong_url_shape"] += 1
            if len(sample_urls["wrong_url_shape"]) < 3:
                sample_urls["wrong_url_shape"].append(result["url"])
            continue
        if _looks_like_listing(result["title"]):
            reasons["listing_title"] += 1
            if len(sample_urls["listing_title"]) < 3:
                sample_urls["listing_title"].append(result["title"])
            continue
        title = _strip_platform_suffix(result["title"], platform)
        stub = _event_stub(title, result["url"], platform, region, _clean_snippet(result["snippet"]))
        events.append(enrich_with_event_page(stub))
        if len(events) >= max_results:
            break
    skipped = sum(reasons.values())
    # Printed unconditionally (not gated on `debug`) - a per-reason
    # breakdown is what actually answers "why did N results become 0
    # events", since a bare skipped-count alone can't distinguish a real
    # filtering bug from the search genuinely surfacing nothing but
    # listing pages/homepages for this query. A few sample URLs/titles
    # for each non-zero reason turn "wrong_url_shape is removing a lot of
    # results" from a guess into something checkable directly against
    # _EVENT_URL_PATTERNS[platform].
    samples = {k: v for k, v in sample_urls.items() if v}
    print(f"[future_events] {platform}: {len(parsed)} result(s) -> {skipped} skipped {reasons}, {len(events)} kept", flush=True)
    if samples:
        print(f"[future_events] {platform}: skip samples {samples}", flush=True)
    if debug is not None and parsed:
        debug[platform] = f"{len(parsed)} result(s), {skipped} filtered as unrelated/listing pages, {len(events)} kept"
    return events


def other_web_events(
    region: str = DEFAULT_REGION,
    max_results: int = 6,
    days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS,
    debug: dict[str, str] | None = None,
) -> list[dict]:
    """Events from anywhere else search turns up for `region` - not
    restricted to Eventbrite/Luma/Meetup via a site: filter, so results
    could be a venue's own site, a local listings site, a ticketing
    platform, ... With no domain allowlist to lean on, a result is only
    kept once it's actually confirmed to be one real, single event - i.e.
    `enrich_with_event_page` found a real schema.org Event date for it,
    not just a plain-text guess (`date_confirmed`) - which is what keeps
    this from filling up with random blog posts, listicles, and social
    media cross-posts (a "Luma (@luma_hq) - Instagram photos and videos"
    result, observed directly, had a plain-text date-shaped substring
    somewhere on the page that had nothing to do with any real event) that
    just happen to mention `region`. `platform_events` accepts either kind
    of date - a plain-text guess is far more trustworthy there, on a page
    already confirmed to be one of the known event platforms. See
    `platform_events` for what `debug` (keyed "web" here) records."""
    query = f"{region} events {_time_window_phrase(days_ahead)}"
    fallback_query = f"{region} events"
    parsed = _search_or_record_failure(query, max_results, debug, "web", fallback_query=fallback_query)
    if parsed is None:
        return []

    known_domains = tuple(PLATFORM_DOMAINS.values())
    events = []
    reasons = {"known_platform_domain": 0, "bare_root_or_listing": 0, "no_confirmed_date": 0}
    for result in parsed:
        domain = urllib.parse.urlparse(result["url"]).netloc.removeprefix("www.")
        if not domain or any(known in domain for known in known_domains):
            reasons["known_platform_domain"] += 1
            continue  # already covered by platform_events - avoid duplicates
        if _is_bare_domain_root(result["url"]) or _looks_like_listing(result["title"]):
            reasons["bare_root_or_listing"] += 1
            continue
        stub = _event_stub(result["title"], result["url"], domain, region, _clean_snippet(result["snippet"]))
        enriched = enrich_with_event_page(stub)
        if not enriched.get("start") or not enriched.get("date_confirmed"):
            reasons["no_confirmed_date"] += 1
            continue  # no confirmed real event date - too likely a listing/blog page to trust
        events.append(enriched)
        if len(events) >= max_results:
            break
    print(f"[future_events] web: {len(parsed)} result(s) -> {sum(reasons.values())} skipped {reasons}, {len(events)} kept", flush=True)
    if debug is not None and parsed:
        debug["web"] = f"{len(parsed)} result(s), {reasons['no_confirmed_date']} dropped for no confirmed date, {len(events)} kept"
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


def dedupe_events(events: list[dict]) -> list[dict]:
    """Drops duplicate events surfaced from more than one source - the
    same real-world event is often independently listed on several
    aggregator sites (allevents.in, stayhappening.com, ... - observed
    directly, same event/time/address, different domain) that each get
    discovered separately by `other_web_events`. Deduplicates by (title,
    start-to-the-hour) once the title is casefolded/whitespace-normalized
    - truncated to the hour since two listings of the same event rarely
    agree on end time or minute/second-level start precision. Falls back
    to (title, location) when start is unknown, so two dateless listings
    of the same event still collapse without wrongly merging two
    different unknown-date events that just share a generic title. Keeps
    whichever entry appears first in `events`."""
    seen = set()
    deduped = []
    for event in events:
        title_key = re.sub(r"\s+", " ", (event.get("title") or "").strip()).casefold()
        start = event.get("start")
        key = (title_key, str(start)[:13]) if start else (title_key, (event.get("location") or "").strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def within_search_window(events: list[dict], days_ahead: int = DEFAULT_SEARCH_WINDOW_DAYS) -> list[dict]:
    """Keeps events with no confirmed start (there's no date to judge, so
    it's shown rather than assumed out of range) plus those starting
    within the next `days_ahead` days; drops ones confirmed to start
    later. This is the actual enforcement of the search window - the
    "this week"/"this month" phrase in the DuckDuckGo query is only a
    bias toward near-term results, not a guarantee, since a lot of pages
    don't literally repeat that phrase back."""
    now = _now_local()
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


# How far back "recent" reaches when picking people/places to suggest for
# a Future tab event - long enough to have real signal, short enough that
# someone/somewhere you saw constantly a couple years ago but haven't
# since doesn't keep outranking who/where you're actually spending time
# with now. Tried before falling back to the same pool's all-time history.
_RECENT_HISTORY_DAYS = 180


def _recent(df: pd.DataFrame) -> pd.DataFrame:
    """`df` narrowed to the last `_RECENT_HISTORY_DAYS` days - empty (not
    a silent fallback to the full frame) when nothing falls in that
    window, so callers control their own fallback ordering explicitly."""
    if df.empty:
        return df
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=_RECENT_HISTORY_DAYS)
    return df[df["start"] >= cutoff]


# Generic address boilerplate - street suffixes, directionals, city/
# state/country - excluded when pulling the "meaningful" words out of an
# address's street-name segment, so two different specific addresses on
# the same street ("575 S Fairfax Ave" and a place you actually go that's
# also on Fairfax) can still match on "fairfax" without every address
# sharing "los"/"angeles"/"ca"/"ave" matching each other regardless of
# street.
_ADDRESS_STOPWORDS = {
    "st", "street", "ave", "avenue", "blvd", "boulevard", "dr", "drive", "rd", "road",
    "ln", "lane", "way", "ct", "court", "pl", "place", "plaza", "cir", "circle",
    "pkwy", "parkway", "ter", "terrace", "hwy", "highway", "sq", "square",
    "n", "s", "e", "w", "ne", "nw", "se", "sw",
    "los", "angeles", "ca", "california", "united", "states", "usa", "us",
}


def _location_keywords(location: str) -> set[str]:
    """Significant words from an address's street-name segment (whichever
    comma-separated segment starts with a house number - "Molly Malone's,
    575 S Fairfax Ave, Los Angeles, CA 90036" puts the venue name first,
    not the street - falling back to the first segment when none starts
    with a number, e.g. a bare "Griffith Park"), with the house number and
    generic street-suffix/directional/city/state/country boilerplate
    stripped. Used as a neighborhood/street-level signal when two
    addresses don't literally contain one another (see
    `_matched_frequent_location`)."""
    if not location:
        return set()
    segments = [s.strip() for s in location.split(",")]
    street_part = next((s for s in segments if re.match(r"^\d", s)), segments[0])
    words = re.findall(r"[A-Za-z]+", street_part)
    return {w.casefold() for w in words if w.casefold() not in _ADDRESS_STOPWORDS and len(w) > 2}


def _matched_frequent_location(df: pd.DataFrame, location: str | None, top_n: int = 15) -> str | None:
    """One of your own most-visited location strings that's the same
    place as `location` (one contains the other, case-insensitively), or
    failing that, on the same street/in the same immediate area (shares a
    meaningful word from the street-name segment - "Fairfax" for two
    different addresses both on Fairfax Ave, say) - a new venue you've
    never been to still gets credit for being somewhere you actually
    spend time around, not just an exact-venue repeat. Tries your
    recently-visited places first, falling back to all-time if nothing
    recent matches - a place you went to constantly two years ago but
    haven't been back to since shouldn't outrank one you're actually
    still going to."""
    if not location or df.empty:
        return None
    location_cf = location.casefold()
    location_keywords = _location_keywords(location)
    for pool in (_recent(df), df):
        if pool.empty:
            continue
        frequented = pool["location"].dropna().value_counts().head(top_n).index
        for place in frequented:
            if place.casefold() in location_cf or location_cf in place.casefold():
                return place
        if location_keywords:
            for place in frequented:
                if location_keywords & _location_keywords(place):
                    return place
    return None


# A one-off encounter shouldn't count as someone you're "overdue" to see
# again - this is the minimum event count (with anyone, anywhere) before
# _most_overdue_person will nudge you to reconnect with them.
_OVERDUE_MIN_EVENTS = 3


def _most_overdue_person(df: pd.DataFrame, exclude: list[str]) -> str | None:
    """Whoever you have a real history with (`_OVERDUE_MIN_EVENTS`+
    events, anywhere) but haven't seen in the longest time, excluding
    anyone already in `exclude` - a reconnection nudge to blend in
    alongside "who you'd normally bring", not instead of it."""
    from felinni import social

    if df.empty:
        return None
    freq = social.person_frequency(df)
    freq = freq[~freq.index.isin(exclude) & (freq["events"] >= _OVERDUE_MIN_EVENTS)]
    if freq.empty:
        return None
    return freq.sort_values("last_seen").index[0]


def suggested_people(df: pd.DataFrame, category: str | None = None, location: str | None = None, limit: int = 3) -> tuple[list[str], str]:
    """People you'd plausibly want to invite, with a reason: your most
    frequent *recent* companions for `category` if it matched one of your
    own, falling back to all-time if you haven't done that category
    recently; then the same recent-first/all-time-fallback pattern for
    whoever you usually go to a matching `location` with; then your most
    frequent companions overall, recent-first too - someone you saw
    constantly a while back but haven't since shouldn't keep outranking
    who you're actually spending time with now.

    The last slot is reserved for a reconnection nudge - whoever you have
    a real history with but haven't seen in the longest time (see
    `_most_overdue_person`) - blended in alongside the category/location
    picks above rather than replacing them, so a suggestion is "who you'd
    normally bring, plus someone you haven't seen in a while" rather than
    one or the other. Returns ([], "...") rather than a name list you
    have no real reason to trust when there's no history to go on at
    all."""
    from felinni import social

    if df.empty:
        return [], "no calendar history to go on yet"

    candidates: list[tuple[pd.DataFrame, str]] = []
    if category:
        cat_pool = df[df["category"].str.casefold() == category.casefold()]
        candidates.append((_recent(cat_pool), f'your usual company for "{category}" recently'))
        candidates.append((cat_pool, f'your usual company for "{category}"'))
    matched_place = _matched_frequent_location(df, location)
    if matched_place:
        place_pool = df[df["location"] == matched_place]
        candidates.append((_recent(place_pool), f"who you've recently gone to {matched_place} with"))
        candidates.append((place_pool, f"who you usually go to {matched_place} with"))
    candidates.append((_recent(df), "your most frequent people recently"))
    candidates.append((df, "your most frequent people overall"))

    base_people: list[str] = []
    base_reason = "no history to go on yet"
    for pool, reason in candidates:
        if pool.empty:
            continue
        freq = social.person_frequency(pool)
        if not freq.empty:
            base_people, base_reason = list(freq.head(limit).index), reason
            break

    overdue = _most_overdue_person(df, exclude=base_people)
    if not overdue:
        return base_people, base_reason

    people = base_people[: max(limit - 1, 0)] + [overdue]
    reason = f"{base_reason}, plus {overdue} (haven't seen them in a while)" if base_people else f"haven't seen {overdue} in a while"
    return people, reason


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
    # Only a confirmed, specific venue (from real schema.org data) is
    # worth matching against your own history - an event whose location
    # is just the generic search region ("Los Angeles, CA", the fallback
    # when there's no real address) will substring-match almost anything
    # in your history that's also broadly in that region, producing a
    # "near <your own specific address>" claim that has nothing to do
    # with actual proximity (observed directly: a Camber roundup with no
    # real venue "matched" a specific address 6+ miles away purely
    # because both strings mention "Los Angeles, CA").
    if not event.get("location_confirmed"):
        return 0.0, []
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

        location_for_people = event.get("location") if event.get("location_confirmed") else None
        people, people_reason = suggested_people(df, category, location_for_people)
        suggestion = dict(event)
        suggestion["matched_category"] = category
        suggestion["fit_score"] = score
        suggestion["fit_reasons"] = reasons
        suggestion["suggested_people"] = people
        suggestion["people_reason"] = people_reason
        ranked.append(suggestion)

    return sorted(ranked, key=lambda e: e["fit_score"], reverse=True)
