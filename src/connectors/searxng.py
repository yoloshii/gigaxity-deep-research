"""SearXNG connector for meta-search.

The instance's own settings.yml (keep_only + categories) is the engine source
of truth. This connector sends `categories=` and, by default, NO `engines=`
parameter: an explicit engines list overrides the instance's `disabled:` flags
and pins every query to exactly those engines, including ones the instance
disabled because they are blocked or return junk.

A vertical lane is the same instance queried on one category (science, it,
videos, docs, packages). It carries its own connector name,
`searxng:<vertical>`, so it fuses as a separate ranked list beside the base
`general` lane. A lane never sends `engines=`: SearXNG adds explicitly named
engines to the requested category's engines, so a pin would pour general
results into every lane.

SearXNG silently answers an undefined category from its default categories,
with no error, and labels each result with its engine's PRIMARY category
rather than the requested one. Result labels therefore cannot show whether a
category exists. The instance's `/config` endpoint can: it lists every engine
with its categories and enabled flag. `instance_categories()` reads it once
per host and caches, per category, the enabled engines that serve it.

A defined category is still not proof the request is honoured: a locked
`categories` preference makes the instance ignore the requested category, and
a `!bang` in the query selects its own engines. So a vertical connector keeps
a result only when one of the engines that returned it serves the category.
"""

import asyncio
import hashlib
import itertools
import logging
import threading
import time
from collections.abc import Mapping

import httpx
from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)

# How long an instance's category map is trusted before `/config` is re-read.
CAPABILITY_TTL_SECONDS = 600.0
# A failed `/config` read is retried sooner than a successful one is refreshed.
CAPABILITY_RETRY_SECONDS = 60.0

# category -> names of the enabled engines that serve it.
CategoryEngines = Mapping[str, frozenset[str]]

# host -> (monotonic expiry, category map, or None when `/config` could not be
# read or had an unexpected shape).
_capabilities: dict[str, tuple[float, CategoryEngines | None]] = {}
# host -> the `/config` read in flight, shared by every caller that needs it.
_inflight: dict[str, asyncio.Task] = {}
# host -> id of the most recently started read. Only that read may update the
# cache: a read started on another event loop is not shared, so two reads of
# one host can overlap and finish in either order.
_latest_read: dict[str, int] = {}
_read_ids = itertools.count(1)
# Guards the three dicts above. Event loops on other threads touch them too,
# so a read's "am I still the latest?" check and its cache write must be one
# step. Held only for dict operations, never across an await; reentrant
# because `create_task` runs under it and an eager task factory would start
# the read (and reach this lock) on the same thread.
_state_lock = threading.RLock()


def _reset_capabilities() -> None:
    """Forget cached instance categories and in-flight reads (tests)."""
    with _state_lock:
        _capabilities.clear()
        _inflight.clear()
        _latest_read.clear()


def _category_engines(payload) -> CategoryEngines | None:
    """Map each category to the enabled engines that serve it, from a `/config` body.

    Strict: one engine entry without a string `name`, a list-of-strings
    `categories` or a boolean `enabled` makes the whole snapshot unusable
    (None). A partial map would report working categories as missing.
    """
    engines = payload.get("engines") if isinstance(payload, dict) else None
    if not isinstance(engines, list) or not engines:
        return None
    by_category: dict[str, set[str]] = {}
    for engine in engines:
        if not isinstance(engine, dict):
            return None
        name = engine.get("name")
        categories = engine.get("categories")
        enabled = engine.get("enabled")
        if not (
            isinstance(name, str)
            and name
            and isinstance(enabled, bool)
            and isinstance(categories, list)
            and all(isinstance(category, str) for category in categories)
        ):
            return None
        if enabled:
            for category in categories:
                by_category.setdefault(category, set()).add(name)
    return {category: frozenset(names) for category, names in by_category.items()}


async def _read_capabilities(key: str, read_id: int) -> CategoryEngines | None:
    """Read `/config` once and cache the outcome for this host."""
    capabilities: CategoryEngines | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{key}/config")
            response.raise_for_status()
            capabilities = _category_engines(response.json())
        if capabilities is None:
            logger.info("SearXNG /config at %s has an unexpected shape", key)
    except Exception as e:
        logger.info("SearXNG /config unreadable at %s: %s", key, type(e).__name__)

    with _state_lock:
        if _latest_read.get(key) != read_id:
            # A newer read of this host has started; its answer is the one kept.
            return capabilities
        current = _capabilities.get(key)
        if capabilities is None and current and current[1] is not None and time.monotonic() < current[0]:
            # A failed read never replaces a successful one that is still fresh.
            return current[1]
        ttl = CAPABILITY_TTL_SECONDS if capabilities is not None else CAPABILITY_RETRY_SECONDS
        _capabilities[key] = (time.monotonic() + ttl, capabilities)
    return capabilities


async def instance_categories(host: str) -> CategoryEngines | None:
    """The enabled engines of each category on this SearXNG instance.

    Read from the instance's `/config` endpoint and cached per host. Callers
    that ask while a read is in flight share it, so a burst of searches makes
    one request per host. Returns None when the endpoint cannot be read or has
    an unexpected shape, so callers can tell "category missing" apart from
    "cannot tell".
    """
    key = host.rstrip("/")
    loop = asyncio.get_running_loop()
    with _state_lock:
        cached = _capabilities.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]

        task = _inflight.get(key)
        if task is None or task.get_loop() is not loop:
            read_id = next(_read_ids)
            _latest_read[key] = read_id
            task = loop.create_task(_read_capabilities(key, read_id))
            _inflight[key] = task

            def _forget(done: asyncio.Task) -> None:
                with _state_lock:
                    if _inflight.get(key) is done:
                        del _inflight[key]

            task.add_done_callback(_forget)
    # One caller giving up must not cancel the read the others are waiting on.
    return await asyncio.shield(task)


def _served_by(result: dict, engines: frozenset[str]) -> bool:
    """Whether one of the engines that returned this result is in `engines`.

    SearXNG merges a URL found by several engines into one result: `engine`
    is the first of them and `engines` lists them all.
    """
    listed = result.get("engines")
    names = {str(name) for name in listed if name} if isinstance(listed, list) else set()
    if result.get("engine"):
        names.add(str(result["engine"]))
    return not names.isdisjoint(engines)


class SearXNGConnector(Connector):
    """SearXNG meta-search connector."""

    name = "searxng"

    def __init__(
        self,
        host: str | None = None,
        engines: str | None = None,
        categories: str | None = None,
        language: str | None = None,
        safesearch: int | None = None,
        vertical: str | None = None,
    ):
        self.host = host or settings.searxng_host
        self.engines = engines if engines is not None else settings.searxng_engines
        self.vertical = vertical
        if vertical:
            # A vertical lane is one category, reported under its own name.
            self.name = f"searxng:{vertical}"
            self.categories = categories or vertical
        else:
            self.categories = categories or settings.searxng_categories
        self.language = language or settings.searxng_language
        self.safesearch = safesearch if safesearch is not None else settings.searxng_safesearch
        # Engines the instance reported as failing on the most recent call
        # (CAPTCHA, suspended, timeout). SearXNG returns HTTP 200 with fewer
        # results when engines fail, so this is the only degradation signal.
        self.last_unresponsive: list[tuple[str, str]] = []
        # For a vertical connector, whether the instance defines the category:
        # "supported", "missing" (not queried), or "unknown" (`/config`
        # unreadable, queried anyway). None for the base lane.
        self.last_category_status: str | None = None
        # For a supported vertical, how many results on the most recent call
        # came only from engines outside the category and were dropped.
        self.last_off_category: int = 0

    def is_configured(self) -> bool:
        return bool(self.host)

    def _probe_url(self) -> str | None:
        # /healthz is SearXNG's documented liveness endpoint (see Troubleshooting).
        return f"{self.host.rstrip('/')}/healthz" if self.host else None

    async def search(
        self,
        query: str,
        top_k: int = 10,
        categories: str | None = None,
    ) -> SearchResult:
        """Execute SearXNG search.

        Args:
            query: Search query.
            top_k: Maximum results to return.
            categories: Per-call category override (comma-separated).
        """
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        self.last_unresponsive = []
        self.last_category_status = None
        self.last_off_category = 0
        # The engines that serve this vertical, when `/config` names them.
        category_engines: frozenset[str] | None = None
        if self.vertical and categories is None:
            # Every vertical, `general` included: a direct `general` search
            # promises general engines, and a locked category preference or a
            # `!bang` can answer from others. (The aggregator's base lane has
            # no vertical and is never filtered.)
            available = await instance_categories(self.host)
            if available is None:
                self.last_category_status = "unknown"
            elif self.vertical in available:
                self.last_category_status = "supported"
                category_engines = available[self.vertical]
            else:
                # Querying it would return the instance's default
                # categories under this lane's name.
                self.last_category_status = "missing"
                logger.info(
                    "SearXNG %s: the instance has no enabled engine in category %r",
                    self.name,
                    self.vertical,
                )
                return SearchResult(sources=[], query=query, connector_name=self.name)

        params = {
            "q": query,
            "format": "json",
            "language": self.language,
        }

        if self.engines:
            params["engines"] = self.engines
        cats = categories if categories is not None else self.categories
        if cats:
            params["categories"] = cats
        if self.safesearch is not None:
            params["safesearch"] = str(self.safesearch)

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.host}/search", params=params)
                response.raise_for_status()
                data = response.json()

            self.last_unresponsive = [
                (str(u[0]), str(u[1]))
                for u in data.get("unresponsive_engines") or []
                if isinstance(u, (list, tuple)) and len(u) >= 2
            ]
            if self.last_unresponsive:
                logger.info("SearXNG %s unresponsive engines: %s", self.name, self.last_unresponsive)

            # Each result keeps its 1-based position in the instance's answer,
            # so dropping one never promotes the results after it.
            ranked = list(enumerate(data.get("results") or [], start=1))
            if category_engines is not None:
                # A locked `categories` preference or a `!bang` in the query
                # makes the instance answer from engines this category lacks.
                served = [(p, r) for p, r in ranked if _served_by(r, category_engines)]
                self.last_off_category = len(ranked) - len(served)
                if self.last_off_category:
                    logger.info(
                        "SearXNG %s: dropped %d results from engines outside the category",
                        self.name,
                        self.last_off_category,
                    )
                ranked = served

            for position, result in ranked[:top_k]:
                url = result.get("url", "")
                source_id = f"sx_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                metadata = {
                    "engine": result.get("engine", ""),
                    "category": result.get("category", ""),
                    "parsed_url": result.get("parsed_url", []),
                    "position": position,
                }
                # Video results carry channel, duration and date.
                for key, meta_key in (
                    ("author", "author"),
                    ("length", "length"),
                    ("publishedDate", "published_date"),
                ):
                    if result.get(key):
                        metadata[meta_key] = result[key]

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=result.get("content", ""),
                    score=1.0 / position,  # Rank-based score
                    connector=self.name,
                    metadata=metadata,
                ))

        except Exception as e:
            logger.warning("SearXNG search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
