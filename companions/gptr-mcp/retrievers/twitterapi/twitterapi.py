"""Native TwitterAPI.io X (Twitter) retriever for gpt-researcher.

The X parallel to the Arctic Shift reddit resolver — but NATIVE SEARCH, not
URL-resolution. TwitterAPI.io's `advanced_search` returns full tweet objects
(text + engagement + author + entities) in one call, so this is a first-class
retriever that sets `raw_content` directly: the conductor (after upstream
f1e51ebe) treats a result with `raw_content` > 100 chars as pre-fetched and
never scrapes it. Read-only and off-IP — TwitterAPI.io's servers touch x.com,
your own IP never does.

Load-bearing invariants (validated via design review):
  * Outgoing requests are host-allowlisted to `api.twitterapi.io` AND
    path-allowlisted to the 4 read GETs (advanced_search, replies/v2,
    thread_context, tweets). Write/state endpoints are unreachable by
    construction; NO Cookie/login_cookie header is ever set, so an account can
    never be acted on. Non-https and 3xx redirects are refused.
  * `>100-or-drop`: every result leaves the retriever carrying real
    `raw_content` > 100 chars, or is dropped — a bare scrapeable x.com href is
    never returned, even on error / missing key / budget exhaustion.
  * Paid-call budget has two layers. (a) Instance per-query caps bound the number
    of search ATTEMPTS / pages (and reply calls) a single sub-query may make. (b)
    The singleton's rolling-window deque is the AUTHORITATIVE physical-spend
    counter: it appends one timestamp per request actually sent (including the one
    retry) and excludes cache hits and budget-denied calls, bounding total spend
    across a long-lived process via monotonic eviction (no run identity needed).
    Reservation happens atomically under the SAME lock that serializes throttle +
    cache + fetch, so concurrent `to_thread` sub-queries cannot double-spend. The
    cache stores SUCCESSFUL payloads only — a denied/failed call is never cached,
    so the window can still reset.
  * The retriever never raises into the conductor — all failures degrade to [].

Stdlib only — no new pip dependency (minimal-venv constraint).
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --- Configuration (env, all optional with safe defaults) -------------------

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_choice(name: str, default: str, allowed: "frozenset[str]") -> str:
    """Read an env value but clamp anything outside `allowed` back to default,
    so a typo'd queryType can never be forwarded to the API."""
    v = (os.getenv(name) or default).strip()
    return v if v in allowed else default


TWITTERAPI_BASE_URL = os.getenv(
    "TWITTERAPI_BASE_URL", "https://api.twitterapi.io"
).rstrip("/")
TWITTERAPI_QUERY_TYPE = _env_choice(
    "TWITTERAPI_QUERY_TYPE", "Top", frozenset({"Top", "Latest"})
)
TWITTERAPI_TIMEOUT = _env_float("TWITTERAPI_TIMEOUT", 15.0)
TWITTERAPI_MAX_CHARS = _env_int("TWITTERAPI_MAX_CHARS", 4000)
TWITTERAPI_MIN_TEXT_CHARS = _env_int("TWITTERAPI_MIN_TWEET_TEXT_CHARS", 0)

# Per-query (instance) caps — bound spend per sub-query, deterministic.
TWITTERAPI_MAX_PAGES_PER_QUERY = _env_int("TWITTERAPI_MAX_PAGES_PER_QUERY", 1)
TWITTERAPI_MAX_SEARCH_CALLS = _env_int("TWITTERAPI_MAX_SEARCH_CALLS_PER_QUERY", 1)
TWITTERAPI_REPLY_TOP_K = _env_int("TWITTERAPI_REPLY_TOP_K", 0)  # 0 => replies OFF
TWITTERAPI_MAX_REPLY_CALLS = _env_int(
    "TWITTERAPI_MAX_REPLY_CALLS_PER_QUERY", TWITTERAPI_REPLY_TOP_K
)
TWITTERAPI_REPLY_QUERY_TYPE = _env_choice(
    "TWITTERAPI_REPLY_QUERY_TYPE", "Relevance",
    frozenset({"Relevance", "Latest", "Likes"})
)
TWITTERAPI_REPLY_MAX = _env_int("TWITTERAPI_REPLY_MAX_PER_TWEET", 5)

# Process-wide rolling-window circuit breaker — the global spend ceiling.
TWITTERAPI_WINDOW_SECONDS = _env_float("TWITTERAPI_WINDOW_SECONDS", 300.0)
TWITTERAPI_MAX_CALLS_PER_WINDOW = _env_int("TWITTERAPI_MAX_CALLS_PER_WINDOW", 60)
TWITTERAPI_MIN_INTERVAL = _env_float("TWITTERAPI_MIN_INTERVAL", 0.5)

# Query-builder operator defaults.
TWITTERAPI_FILTER_RETWEETS = _env_bool("TWITTERAPI_FILTER_RETWEETS", True)
TWITTERAPI_QUERY_LANG = os.getenv("TWITTERAPI_QUERY_LANG", "").strip()
TWITTERAPI_SINCE_TIME = _env_int("TWITTERAPI_SINCE_TIME", 0)  # UNIX epoch, 0=off
TWITTERAPI_UNTIL_TIME = _env_int("TWITTERAPI_UNTIL_TIME", 0)  # UNIX epoch, 0=off

_USER_AGENT = "gpt-researcher twitterapi retriever (read-only research)"
_MIN_RAW_CONTENT = 100  # conductor's prefetch threshold (must be EXCEEDED)
_ALLOWED_HOST = "api.twitterapi.io"
# ONLY these read GETs may be requested. Write/state paths are unreachable.
_ALLOWED_PATHS = frozenset({
    "/twitter/tweet/advanced_search",
    "/twitter/tweet/replies/v2",
    "/twitter/tweet/thread_context",
    "/twitter/tweets",
})


# --- Safe HTTP (TwitterAPI.io read endpoints only) --------------------------

class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse ALL 3xx redirects so a response cannot bounce us off the API host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirect refused -> {newurl}", headers, fp
        )


_OPENER = urllib.request.build_opener(_RefuseRedirect())


def _http_get_json(url: str, api_key: str, timeout: float) -> Any:
    """GET JSON from an allowlisted TwitterAPI.io read endpoint.

    Refuses non-https, any host other than api.twitterapi.io, and any path
    outside the read allowlist (so a write/state endpoint can never be hit even
    by a coding bug). Sets ONLY the X-API-Key / Accept / User-Agent headers — no
    Cookie / login_cookie is ever attached, so the request cannot act on an
    account.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"refusing non-https url: {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if host != _ALLOWED_HOST:
        raise ValueError(f"host not allowlisted (TwitterAPI.io only): {host!r}")
    if parts.path not in _ALLOWED_PATHS:
        raise ValueError(f"path not allowlisted (read endpoints only): {parts.path!r}")

    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


# --- Process-wide budget gate (window cap + throttle + cache) ---------------

class _BudgetGate:
    """Singleton gate that every outbound TwitterAPI.io call passes through.

    One lock spans cache-check -> window-evict -> window-cap-check -> throttle ->
    reserve -> fetch -> cache-write, so concurrent `to_thread` sub-queries cannot
    double-spend the paid budget. The rolling window is a monotonic-timestamp
    deque: the call rate is bounded to `max_calls` per `window_seconds` with an
    implicit reset (old timestamps evicted) — no run identity required. Cache
    keys are per-endpoint tuples (free hits bypass the paid counter, so the key
    must capture every input that changes the response).
    """

    def __init__(
        self,
        window_seconds: float = TWITTERAPI_WINDOW_SECONDS,
        max_calls: int = TWITTERAPI_MAX_CALLS_PER_WINDOW,
        min_interval: float = TWITTERAPI_MIN_INTERVAL,
    ) -> None:
        self._lock = threading.Lock()
        self._window: "deque[float]" = deque()
        self._window_seconds = max(0.0, window_seconds)
        self._max_calls = max(0, max_calls)
        self._min_interval = max(0.0, min_interval)
        self._next_allowed = 0.0
        # cache_key -> payload (success) or None (tried & failed/denied this run)
        self._cache: Dict[Tuple[Any, ...], Optional[Any]] = {}

    def fetch(
        self, cache_key: Tuple[Any, ...], url: str, api_key: str, timeout: float
    ) -> Optional[Any]:
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            payload = self._do_fetch(url, api_key, timeout)
            # Cache SUCCESSFUL payloads only. A real response is always a dict
            # (even `{"tweets": []}`); `None` means budget-denied or errored.
            # Never caching None means a denial/transient failure does NOT poison
            # the key — once the rolling window evicts, the same key re-checks the
            # budget and can succeed (the cleared design's reset semantics).
            if payload is not None:
                self._cache[cache_key] = payload
            return payload

    def _reserve(self) -> bool:
        """Caller holds the lock. Evict expired timestamps, enforce the window
        cap, and reserve THIS call by appending now. Returns False (deny) when
        the window is full — the caller then degrades without opening a socket."""
        now = time.monotonic()
        cutoff = now - self._window_seconds
        while self._window and self._window[0] < cutoff:
            self._window.popleft()
        if len(self._window) >= self._max_calls:
            return False
        self._window.append(now)
        return True

    def _do_fetch(self, url: str, api_key: str, timeout: float) -> Optional[Any]:
        # Each attempt reserves its OWN window slot before its socket opens, so a
        # retry is counted as a second billable call (and is itself denied if the
        # window is now full).
        for attempt in range(2):
            if not self._reserve():
                logger.warning("twitterapi budget window exhausted; degrading")
                return None
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            try:
                payload = _http_get_json(url, api_key, timeout)
                self._next_allowed = time.monotonic() + self._min_interval
                return payload
            except urllib.error.HTTPError as e:
                self._next_allowed = time.monotonic() + self._min_interval
                if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0 + attempt)
                    continue
                logger.warning("twitterapi HTTP %s for %s", e.code, _path_of(url))
                return None
            except (urllib.error.URLError, ValueError, OSError, json.JSONDecodeError) as e:
                logger.warning("twitterapi fetch failed for %s: %s", _path_of(url), e)
                return None
        return None


def _path_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).path or url
    except ValueError:
        return url


_DEFAULT_GATE: Optional[_BudgetGate] = None
_DEFAULT_GATE_LOCK = threading.Lock()


def get_default_gate() -> _BudgetGate:
    """Process-wide budget gate (the conductor builds a fresh retriever per
    sub-query, so the window cap + throttle + cache must live on a shared
    singleton to be process-wide)."""
    global _DEFAULT_GATE
    if _DEFAULT_GATE is None:
        with _DEFAULT_GATE_LOCK:
            if _DEFAULT_GATE is None:
                _DEFAULT_GATE = _BudgetGate()
    return _DEFAULT_GATE


# --- Query builder -----------------------------------------------------------

def build_query(raw: str) -> str:
    """Build an advanced_search `query` value from a research sub-query plus
    configured operators. Time bounds are UNIX epoch only (`since_time:` /
    `until_time:`) — the calendar `since:`/`until:` form is no longer supported
    by the API. The whole value is URL-encoded by the caller via urlencode."""
    core = (raw or "").strip()
    parts: List[str] = [core] if core else []
    if TWITTERAPI_FILTER_RETWEETS:
        parts.append("-filter:retweets")
    if TWITTERAPI_QUERY_LANG:
        parts.append(f"lang:{TWITTERAPI_QUERY_LANG}")
    if TWITTERAPI_SINCE_TIME > 0:
        parts.append(f"since_time:{TWITTERAPI_SINCE_TIME}")
    if TWITTERAPI_UNTIL_TIME > 0:
        parts.append(f"until_time:{TWITTERAPI_UNTIL_TIME}")
    return " ".join(parts).strip()


# --- Formatting --------------------------------------------------------------

def _num(v: Any) -> Optional[int]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _engagement_line(tw: Dict[str, Any]) -> str:
    bits = []
    for key, label in (
        ("likeCount", "likes"), ("retweetCount", "RTs"),
        ("replyCount", "replies"), ("quoteCount", "quotes"),
        ("viewCount", "views"),
    ):
        n = _num(tw.get(key))
        if n is not None:
            bits.append(f"{int(n)} {label}")
    return "[" + ", ".join(bits) + "]" if bits else ""


def format_tweet(
    tw: Dict[str, Any],
    max_chars: int = TWITTERAPI_MAX_CHARS,
    min_text_chars: int = TWITTERAPI_MIN_TEXT_CHARS,
) -> Optional[Dict[str, Any]]:
    """Render one tweet object into a retriever result with `raw_content`, or
    None if it is too thin. Guarantees `raw_content` > 100 so the result is
    treated as pre-fetched and the x.com href is never scraped."""
    if not isinstance(tw, dict):
        return None
    text = (tw.get("text") or "").strip()
    if not text:
        return None  # min-content floor: require real tweet text
    author = tw.get("author") if isinstance(tw.get("author"), dict) else {}
    handle = (author.get("userName") or author.get("username") or "?").strip() or "?"
    name = (author.get("name") or "").strip()
    quoted = tw.get("quoted_tweet") if isinstance(tw.get("quoted_tweet"), dict) else {}
    quoted_text = (quoted.get("text") or "").strip()
    if len(text) + len(quoted_text) < max(0, min_text_chars):
        return None  # configured thin-content drop (default off)

    tweet_id = str(tw.get("id") or tw.get("tweetId") or "").strip()
    url = (tw.get("url") or "").strip()
    if not url and tweet_id and handle != "?":
        url = f"https://x.com/{handle}/status/{tweet_id}"
    if not url:
        return None  # no usable href -> malformed result; drop rather than emit it

    followers = _num(author.get("followers"))
    meta = []
    if followers is not None:
        meta.append(f"{int(followers)} followers")
    if author.get("isBlueVerified"):
        meta.append("verified")
    created = (tw.get("createdAt") or "").strip()

    header = f"@{handle}" + (f" ({name})" if name else "")
    if meta:
        header += " · " + ", ".join(meta)
    if created:
        header += " · " + created

    lines = [f"[X/Twitter post — {header}]", text]
    if quoted_text:
        q_handle = ""
        q_author = quoted.get("author") if isinstance(quoted.get("author"), dict) else {}
        if q_author:
            q_handle = (q_author.get("userName") or q_author.get("username") or "").strip()
        lines.append(f"Quoting{(' @' + q_handle) if q_handle else ''}: {quoted_text}")
    eng = _engagement_line(tw)
    if eng:
        lines.append(eng)

    raw = "\n".join(lines).strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + " …"
    if len(raw) <= _MIN_RAW_CONTENT:
        return None  # >100-or-drop: never hand back a bare scrapeable href

    title = f"@{handle}: {text[:120]}" + ("…" if len(text) > 120 else "")
    return {
        "href": url,
        "title": title,
        "body": text[:280],
        "raw_content": raw,
        "_tweet_id": tweet_id,  # internal, stripped before return
        "_raw_lines": lines,    # internal, reused when appending replies
    }


def _append_replies(item: Dict[str, Any], replies: List[Dict[str, Any]],
                    max_replies: int, max_chars: int) -> None:
    """Append a 'Top replies' block to an item's raw_content in place."""
    rendered = []
    seen = set()
    for r in replies:
        if not isinstance(r, dict):
            continue
        rtext = (r.get("text") or "").strip()
        if not rtext:
            continue
        key = str(r.get("id") or " ".join(rtext.lower().split()))
        if key in seen:
            continue
        seen.add(key)
        rauthor = r.get("author") if isinstance(r.get("author"), dict) else {}
        rhandle = (rauthor.get("userName") or rauthor.get("username") or "?").strip() or "?"
        rendered.append(f"  - @{rhandle}: {' '.join(rtext.split())}")
        if len(rendered) >= max_replies:
            break
    if not rendered:
        return
    lines = list(item.get("_raw_lines") or [item["raw_content"]])
    lines.append(f"Top replies ({len(rendered)}):")
    lines.extend(rendered)
    raw = "\n".join(lines).strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + " …"
    item["raw_content"] = raw
    item["_raw_lines"] = lines


# --- Retriever ---------------------------------------------------------------

class TwitterAPISearch:
    """Native X/Twitter retriever backed by TwitterAPI.io `advanced_search`.

    Returns GPT-Researcher results [{title, href, body, raw_content}, ...] where
    every item carries `raw_content` > 100 chars, so the conductor uses the tweet
    text directly and never scrapes x.com. Set TWITTERAPI_IO_KEY in the env; with
    no key the retriever returns [] (it never emits a bare x.com href).
    """

    def __init__(self, query, query_domains=None, **kwargs) -> None:
        # `**kwargs` keeps the constructor robust across both the active
        # `retriever_class(query, query_domains=...)` call and the dormant
        # `_search()` path that passes extra kwargs (xquik accepts **kwargs).
        self.query = query
        self.query_domains = query_domains
        self.api_key = os.getenv("TWITTERAPI_IO_KEY")
        self.base_url = TWITTERAPI_BASE_URL
        self.query_type = TWITTERAPI_QUERY_TYPE
        self.timeout = TWITTERAPI_TIMEOUT
        self.max_pages = max(1, TWITTERAPI_MAX_PAGES_PER_QUERY)
        self.max_search_calls = max(1, TWITTERAPI_MAX_SEARCH_CALLS)
        self.reply_top_k = max(0, TWITTERAPI_REPLY_TOP_K)
        self.max_reply_calls = max(0, TWITTERAPI_MAX_REPLY_CALLS)
        self.reply_query_type = TWITTERAPI_REPLY_QUERY_TYPE
        self.reply_max = max(1, TWITTERAPI_REPLY_MAX)
        self.max_chars = TWITTERAPI_MAX_CHARS
        self.min_text_chars = TWITTERAPI_MIN_TEXT_CHARS

    def search(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search X via TwitterAPI.io. Returns [] on any failure / missing key —
        never raises into the conductor, never returns a bare scrapeable href."""
        if not self.api_key:
            logger.info("TwitterAPISearch: TWITTERAPI_IO_KEY unset -> no X results")
            return []
        try:
            return self._search(max_results)
        except Exception as e:  # the retriever must never break the conductor
            logger.error("TwitterAPISearch error: %s", e)
            return []

    def _search(self, max_results: int) -> List[Dict[str, Any]]:
        gate = get_default_gate()
        query = build_query(self.query)
        if not query:
            return []

        results: List[Dict[str, Any]] = []
        cursor = ""
        # `pages`/`searches` bound search ATTEMPTS (loop iterations) per query —
        # a hard guard against an unbounded cursor loop. Authoritative paid-spend
        # accounting (sent requests incl. retries, minus cache hits/denials) is
        # the gate's rolling-window deque, not these counters.
        pages = 0
        searches = 0
        while (
            len(results) < max_results
            and pages < self.max_pages
            and searches < self.max_search_calls
        ):
            params = {"query": query, "queryType": self.query_type}
            if cursor:
                params["cursor"] = cursor
            url = f"{self.base_url}/twitter/tweet/advanced_search?{urllib.parse.urlencode(params)}"
            cache_key = ("advanced_search", query, self.query_type, cursor)
            searches += 1
            pages += 1
            payload = gate.fetch(cache_key, url, self.api_key, self.timeout)
            if not payload:
                break  # budget exhausted / error -> stop (degrade)
            tweets = payload.get("tweets") if isinstance(payload, dict) else None
            if not isinstance(tweets, list):
                break
            for tw in tweets:
                item = format_tweet(tw, self.max_chars, self.min_text_chars)
                if item is not None:
                    results.append(item)
                    if len(results) >= max_results:
                        break
            cursor = (payload.get("next_cursor") or "") if isinstance(payload, dict) else ""
            if not (isinstance(payload, dict) and payload.get("has_next_page") and cursor):
                break

        if self.reply_top_k > 0 and self.max_reply_calls > 0 and results:
            self._enrich_replies(gate, results)

        # Strip internal bookkeeping keys before handing results to the conductor.
        out = []
        for item in results[:max_results]:
            out.append({k: v for k, v in item.items() if not k.startswith("_")})
        return out

    def _enrich_replies(self, gate: _BudgetGate, results: List[Dict[str, Any]]) -> None:
        """Opt-in: append top replies to the top-K tweets (the reddit 'top
        comments' analog). Bounded by reply_top_k AND max_reply_calls AND the
        gate's window budget."""
        reply_calls = 0
        for item in results[: self.reply_top_k]:
            if reply_calls >= self.max_reply_calls:
                break
            tweet_id = item.get("_tweet_id")
            if not tweet_id:
                continue
            params = {"tweetId": tweet_id, "queryType": self.reply_query_type}
            url = f"{self.base_url}/twitter/tweet/replies/v2?{urllib.parse.urlencode(params)}"
            cache_key = ("replies", str(tweet_id), self.reply_query_type, "")
            reply_calls += 1
            payload = gate.fetch(cache_key, url, self.api_key, self.timeout)
            if not payload:
                break  # budget exhausted -> stop enriching
            replies = None
            if isinstance(payload, dict):
                replies = payload.get("tweets") or payload.get("replies")
            if isinstance(replies, list) and replies:
                _append_replies(item, replies, self.reply_max, self.max_chars)
