"""Off-IP Reddit comment resolver for the social_openai retriever.

Reddit aggressively blocks scrapers (403) and rate-limits by source IP, so this
resolver never contacts reddit.com directly. The gpt-researcher conductor, after
the upstream f1e51ebe change, only treats a retriever result as pre-fetched content
(and therefore does NOT scrape it) when the result carries `raw_content` longer
than 100 chars. So this module fetches a Reddit thread's comments from the
**Arctic Shift archive API** (`arctic-shift.photon-reddit.com`, NOT reddit.com)
and the retriever sets that text as `raw_content` — the conductor uses it
directly and never scrapes reddit.com.

Load-bearing invariants (validated via design review):
  * NEVER construct or request a reddit.com URL. We READ a reddit permalink only
    to extract the post id, then request Arctic Shift. Outgoing requests are
    host-allowlisted to *.photon-reddit.com and refuse redirects.
  * `>100-or-drop`: a recognized reddit URL must leave the retriever with
    `raw_content` > 100 chars, or be dropped from results — never handed back as
    a bare scrapeable href. This holds even when ARCTIC_SHIFT_ENABLED is false
    (the API call is skipped, but snippet-fallback-or-drop still applies).
  * Backend-pluggable: `RedditCommentResolver` runs an ordered list of backends;
    v1 ships `ArcticShiftBackend`. A future Apify-actor backend (also off-IP) can
    be appended before the snippet fallback without touching the retriever seam.

Stdlib only — no new pip dependency (minimal venv constraint).
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Protocol

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


ARCTIC_SHIFT_ENABLED = _env_bool("ARCTIC_SHIFT_ENABLED", True)
ARCTIC_SHIFT_BASE_URL = os.getenv(
    "ARCTIC_SHIFT_BASE_URL", "https://arctic-shift.photon-reddit.com/api"
).rstrip("/")
ARCTIC_SHIFT_FETCH_LIMIT = _env_int("ARCTIC_SHIFT_FETCH_LIMIT", 100)
ARCTIC_SHIFT_MAX_COMMENTS = _env_int("ARCTIC_SHIFT_MAX_COMMENTS", 30)
ARCTIC_SHIFT_MAX_CHARS = _env_int("ARCTIC_SHIFT_MAX_CHARS", 6000)
ARCTIC_SHIFT_TIMEOUT = _env_float("ARCTIC_SHIFT_TIMEOUT", 15.0)
ARCTIC_SHIFT_MIN_INTERVAL = _env_float("ARCTIC_SHIFT_MIN_INTERVAL", 0.6)

_USER_AGENT = "gpt-researcher social_openai reddit_resolver (read-only research)"
_MIN_RAW_CONTENT = 100  # conductor's prefetch threshold (must be EXCEEDED)

# --- URL parsing -------------------------------------------------------------

# base36 reddit post id inside a permalink, or a redd.it short link.
_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)
_SHORTLINK_RE = re.compile(r"^/([a-z0-9]+)/?$", re.IGNORECASE)
_ID_OK_RE = re.compile(r"^[a-z0-9]+$", re.IGNORECASE)


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_reddit_url(url: str) -> bool:
    """True for any reddit.com / redd.it input URL (so it is never scraped).

    NOTE: this checks an INPUT permalink for routing only — we never REQUEST it.
    Uses exact host / dotted-suffix matching so `notreddit.com` does not match.
    """
    host = _host_of(url)
    if not host:
        return False
    return (
        host == "reddit.com"
        or host.endswith(".reddit.com")
        or host == "redd.it"
        or host.endswith(".redd.it")
    )


def parse_post_id(url: str) -> Optional[str]:
    """Extract the base36 post id from a Reddit thread permalink, else None.

    Handles www/old/np/m subdomains, trailing slug, comment-permalinks (the
    thread id is still the /comments/<id> segment), query/fragment, and
    redd.it/<id> short links. Returns None for non-thread reddit URLs
    (subreddit / user / search pages) and non-reddit URLs.
    """
    if not url or not is_reddit_url(url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    path = parts.path or ""

    m = _POST_ID_RE.search(path)
    if m:
        pid = m.group(1)
        return pid if _ID_OK_RE.match(pid) else None

    # redd.it/<id> short link — id is the whole path
    if _host_of(url) in ("redd.it",) or _host_of(url).endswith(".redd.it"):
        m = _SHORTLINK_RE.match(path)
        if m:
            pid = m.group(1)
            return pid if _ID_OK_RE.match(pid) else None
    return None


# --- Safe HTTP (Arctic Shift only) ------------------------------------------

class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse ALL 3xx redirects so a response cannot bounce us to reddit.com."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirect refused -> {newurl}", headers, fp
        )


_OPENER = urllib.request.build_opener(_RefuseRedirect())


def _host_allowed(host: str) -> bool:
    return (
        host == "arctic-shift.photon-reddit.com"
        or host == "photon-reddit.com"
        or host.endswith(".photon-reddit.com")
    )


def _http_get_json(url: str, timeout: float) -> Any:
    """GET JSON from an allowlisted https Arctic Shift URL. Raises on anything
    suspicious (wrong scheme/host) so a bug can never leak a request elsewhere."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"refusing non-https url: {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if not _host_allowed(host):
        raise ValueError(f"host not allowlisted (Arctic Shift only): {host!r}")

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


# --- Backend protocol + Arctic Shift implementation -------------------------

class CommentBackend(Protocol):
    name: str

    def fetch_comments(self, post_id: str) -> List[Dict[str, Any]]:
        """Return a list of comment dicts ({body, author, score, ...}) or []."""
        ...


class ArcticShiftBackend:
    """Fetch a thread's flat comment list from the Arctic Shift archive API.

    Off-IP (archive host, not reddit.com). Caches the RAW comment list per post
    id (not the formatted text — formatting is done per-result with that result's
    own title/body). A process-wide lock serializes calls + enforces a min
    interval (polite to the volunteer API) and dedups concurrent in-flight calls.
    """

    name = "arctic_shift"

    def __init__(
        self,
        base_url: str = ARCTIC_SHIFT_BASE_URL,
        fetch_limit: int = ARCTIC_SHIFT_FETCH_LIMIT,
        timeout: float = ARCTIC_SHIFT_TIMEOUT,
        min_interval: float = ARCTIC_SHIFT_MIN_INTERVAL,
    ) -> None:
        # Validate the configured host up front — refuse a reddit.com base url.
        host = _host_of(base_url)
        if not _host_allowed(host):
            raise ValueError(
                f"ARCTIC_SHIFT_BASE_URL host not allowlisted: {host!r} "
                "(must be *.photon-reddit.com)"
            )
        self.base_url = base_url.rstrip("/")
        self.fetch_limit = max(1, min(100, fetch_limit))  # API caps at 100
        self.timeout = timeout
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        # post_id -> List[dict] (success) or None (tried & failed this run)
        self._cache: Dict[str, Optional[List[Dict[str, Any]]]] = {}

    def fetch_comments(self, post_id: str) -> List[Dict[str, Any]]:
        if not post_id or not _ID_OK_RE.match(post_id):
            return []
        # Hold the lock across cache-check + throttle + fetch + cache-write:
        # this serializes calls (polite, single-flight) and dedups concurrent
        # to_thread callers for the same post id.
        with self._lock:
            if post_id in self._cache:
                cached = self._cache[post_id]
                return list(cached) if cached else []

            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)

            comments = self._do_fetch(post_id)
            self._next_allowed = time.monotonic() + self.min_interval
            self._cache[post_id] = comments  # may be None -> negative-cache
            return list(comments) if comments else []

    def _do_fetch(self, post_id: str) -> Optional[List[Dict[str, Any]]]:
        url = (
            f"{self.base_url}/comments/search"
            f"?link_id=t3_{urllib.parse.quote(post_id)}&limit={self.fetch_limit}"
        )
        for attempt in range(2):  # 1 retry on 429/5xx
            try:
                payload = _http_get_json(url, self.timeout)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0 + attempt)
                    continue
                logger.warning("ArcticShift HTTP %s for t3_%s", e.code, post_id)
                return None
            except (urllib.error.URLError, ValueError, OSError, json.JSONDecodeError) as e:
                logger.warning("ArcticShift fetch failed for t3_%s: %s", post_id, e)
                return None

            data = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(data, list):
                logger.warning("ArcticShift unexpected shape for t3_%s", post_id)
                return None
            return [c for c in data if isinstance(c, dict)]
        return None


# --- Formatting --------------------------------------------------------------

_BAD_BODIES = {"", "[deleted]", "[removed]", "[deleted by user]"}


def _snippet_block(title: str, body: str, note: str) -> str:
    title = (title or "").strip()
    body = (body or "").strip()
    parts = [f"[Reddit thread — {note}]"]
    if title:
        parts.append(title)
    if body:
        parts.append(body)
    return "\n".join(parts).strip()


def format_comments(
    comments: List[Dict[str, Any]],
    title: str = "",
    body: str = "",
    max_comments: int = ARCTIC_SHIFT_MAX_COMMENTS,
    max_chars: int = ARCTIC_SHIFT_MAX_CHARS,
) -> str:
    """Render a thread's comments as a readable block, prefixed with the OpenAI
    title/snippet (OP context). Preserves Arctic Shift's returned order; uses
    `score` only as a secondary tiebreaker when scores are present (scores lag
    ~36h on fresh threads, so order is the primary signal)."""
    usable = []
    seen = set()
    for c in comments:
        text = (c.get("body") or "").strip()
        if text.lower() in _BAD_BODIES or not text:
            continue
        # Dedup by comment id when present, else by normalized body text.
        key = c.get("id") or " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        usable.append(c)

    # Preserve Arctic Shift's returned API order (the design's PRIMARY signal).
    # Scores lag ~36h on fresh threads, so we do NOT reorder by score — score is
    # shown as metadata only.

    header_parts = []
    if (title or "").strip():
        header_parts.append((title or "").strip())
    if (body or "").strip():
        header_parts.append((body or "").strip())
    header = ("\n".join(header_parts)).strip()

    lines: List[str] = []
    if header:
        lines.append(header)
        lines.append("")
    lines.append(f"Top comments ({min(len(usable), max_comments)} of {len(usable)}):")
    for c in usable[:max_comments]:
        author = c.get("author") or "?"
        score = c.get("score")
        sval = f" (score={score})" if isinstance(score, (int, float)) else ""
        body_txt = " ".join((c.get("body") or "").split())
        lines.append(f"- u/{author}{sval}: {body_txt}")

    out = "\n".join(lines).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + " …"
    return out


# --- Resolver ----------------------------------------------------------------

class RedditCommentResolver:
    """Resolve a Reddit URL to comment text via off-IP backends, or fall back to
    the OpenAI snippet. Returns text > 100 chars, or None (caller then DROPS the
    result so a reddit URL is never handed to the scraper)."""

    def __init__(self, backends: Optional[List[CommentBackend]] = None) -> None:
        if backends is not None:
            self.backends = backends
        elif ARCTIC_SHIFT_ENABLED:
            try:
                self.backends = [ArcticShiftBackend()]
            except ValueError as e:
                logger.error("ArcticShiftBackend disabled (bad config): %s", e)
                self.backends = []
        else:
            self.backends = []

    def is_reddit_url(self, url: str) -> bool:
        return is_reddit_url(url)

    def resolve(self, url: str, title: str = "", body: str = "") -> Optional[str]:
        """Returns comment text (or honest snippet fallback) > 100 chars, else None.

        Guarantees the caller never gets a scrapeable reddit href back: either a
        substantial block is returned, or None (→ caller drops the result).
        """
        if not is_reddit_url(url):
            return None  # caller only routes reddit URLs here

        post_id = parse_post_id(url)
        if post_id:
            for backend in self.backends:
                try:
                    comments = backend.fetch_comments(post_id)
                except Exception as e:  # backend must never break the retriever
                    logger.warning("backend %s failed for t3_%s: %s",
                                   getattr(backend, "name", "?"), post_id, e)
                    comments = []
                if comments:
                    text = format_comments(comments, title=title, body=body)
                    if len(text) > _MIN_RAW_CONTENT:
                        return text
            # thread known but no comments fetched -> snippet fallback
            fb = _snippet_block(title, body, "archive comments unavailable; search snippet")
            return fb if len(fb) > _MIN_RAW_CONTENT else None

        # Non-thread reddit URL (subreddit/user/search) -> snippet only, never scrape
        fb = _snippet_block(title, body, "non-thread reddit page; search snippet")
        return fb if len(fb) > _MIN_RAW_CONTENT else None


# --- Process-wide singleton --------------------------------------------------
# The conductor builds a fresh retriever instance per sub-query, so the throttle
# + cache must live on a shared singleton (not per-instance) to be process-wide.

_DEFAULT_RESOLVER: Optional["RedditCommentResolver"] = None
_DEFAULT_RESOLVER_LOCK = threading.Lock()


def get_default_resolver() -> "RedditCommentResolver":
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        with _DEFAULT_RESOLVER_LOCK:
            if _DEFAULT_RESOLVER is None:
                _DEFAULT_RESOLVER = RedditCommentResolver()
    return _DEFAULT_RESOLVER
