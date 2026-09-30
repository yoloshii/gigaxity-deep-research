"""SearXNG vertical lanes, the Parallel connector and the vertical_search tool.

Bugs these catch:
- the SearXNG connector sending an `engines=` list by default, which overrides
  the instance's `disabled:` flags and pins every query to engines the
  instance disabled;
- an `engines=` pin leaking into a lane: SearXNG adds named engines to the
  requested category, so the lane would carry general results;
- a lane on an instance that does not define its category: SearXNG answers
  from its defaults under the lane's name, and result labels cannot show it
  (each result carries its engine's PRIMARY category), so capability must come
  from the instance's /config;
- a defined category that the request still does not get: a locked category
  preference or a `!bang` makes the instance answer from other engines, so
  only results an engine of the category returned may carry the lane's name;
- a malformed /config cached as "category missing", a /config read per
  concurrent search, a failed read overwriting a good one, and a slow read
  holding back every provider;
- a URL counted twice because the base lane and a lane of the same instance
  both returned it, and the de-dup promoting the lane results after it;
- a vertical lane that is not fused as its own list, fires when routing is
  off, or fires for an explicit `general` focus;
- REST /discover ignoring its focus mode and connector filter;
- the Parallel connector omitting `mode` (the API then bills `advanced`, 5x fast),
  not capping `max_results` (extra results bill), retrying on every call after
  a 402 "insufficient credit", or letting one key's backoff block another;
- the REST and HTTP-MCP surfaces drifting from the stdio MCP tool set, cache
  keys that ignore the lane or the connector filter, and a connector filter
  (`["*"]`, a name holding a comma) that collides with another filter's key.
"""

import asyncio
import json
import threading
import time

import httpx
import pytest

from src import mcp_server
from src.config import Settings, settings
from src.connectors import ParallelConnector, SearXNGConnector
from src.connectors import parallel as parallel_mod
from src.connectors import searxng as searxng_mod
from src.connectors.base import Connector, SearchResult, Source
from src.search import SearchAggregator
from src.search.verticals import classify_vertical, resolve_vertical

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _clean_module_state():
    searxng_mod._reset_capabilities()
    parallel_mod._reset_backoff()
    yield
    searxng_mod._reset_capabilities()
    parallel_mod._reset_backoff()


def _mock_http(monkeypatch, handler):
    """Route every httpx.AsyncClient through a MockTransport; return the request log."""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


# An instance's /config: `docs` exists only through a disabled engine, so it
# is not a usable category.
SX_CONFIG = {
    "engines": [
        {"name": "duckduckgo", "categories": ["general", "web"], "enabled": True},
        {"name": "arxiv", "categories": ["science", "scientific publications"], "enabled": True},
        {"name": "stackoverflow", "categories": ["it", "q&a"], "enabled": True},
        {"name": "youtube", "categories": ["videos", "music"], "enabled": True},
        {"name": "pypi", "categories": ["packages", "other"], "enabled": True},
        {"name": "mdn", "categories": ["docs", "other"], "enabled": False},
    ],
}


def _sx(payload, config=SX_CONFIG, config_status=200, by_category=None):
    """A SearXNG handler: /config answers `config`, /search answers `payload`
    (or `by_category[<categories param>]` when given)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/config":
            return httpx.Response(config_status, json=config)
        if by_category is not None:
            return httpx.Response(200, json=by_category[request.url.params.get("categories")])
        return httpx.Response(200, json=payload)

    return handler


def _searches(calls):
    return [c for c in calls if c.url.path == "/search"]


def _configs(calls):
    return [c for c in calls if c.url.path == "/config"]


SX_PAYLOAD = {
    "results": [
        {
            "url": "https://www.youtube.com/watch?v=abc",
            "title": "SearXNG setup",
            "content": "A walkthrough.",
            "engine": "youtube",
            "category": "videos",
            "author": "Chan",
            "length": "4:29",
            "publishedDate": None,
        },
        {
            "url": "https://example.org/b",
            "title": "B",
            "content": "b",
            "engine": "duckduckgo",
            "category": "general",
        },
    ],
    "unresponsive_engines": [["startpage", "CAPTCHA"]],
}

PX_PAYLOAD = {
    "search_id": "search_x",
    "results": [
        {"url": "https://a.example/1", "title": "A", "publish_date": "2026-09-01", "excerpts": ["one", "two"]},
        {"url": "", "title": "no url", "excerpts": ["dropped"]},
        {"url": "https://a.example/2", "title": None, "excerpts": []},
    ],
    "session_id": "session_x",
}


# An engine of each category in SX_CONFIG.
_ENGINE = {"general": "duckduckgo", "science": "arxiv", "it": "stackoverflow", "videos": "youtube", "packages": "pypi"}


def _results(*urls, category="general", engine=None):
    return {
        "results": [
            {"url": u, "title": u, "content": u, "engine": engine or _ENGINE[category], "category": category}
            for u in urls
        ],
        "unresponsive_engines": [],
    }


class TestVerticalRouting:
    """Query -> vertical lane selection."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("recent arxiv papers on speculative decoding", "science"),
            ("peer-reviewed meta-analysis of intermittent fasting", "science"),
            ("python traceback KeyError in pandas groupby", "it"),
            ("how to fix docker compose not working after upgrade", "it"),
            ("searxng setup tutorial", "videos"),
            ("how-to set up nginx as a reverse proxy", "videos"),
            ("Tavily API pricing September 2026", None),
            ("compare Brave and Exa search APIs", None),
        ],
    )
    def test_classify_vertical(self, query, expected):
        assert classify_vertical(query) == expected

    @pytest.mark.unit
    def test_focus_modes_map_to_verticals(self):
        assert resolve_vertical("anything", "academic") == "science"
        assert resolve_vertical("anything", "debugging") == "it"
        assert resolve_vertical("anything", "documentation") == "it"
        assert resolve_vertical("anything", "tutorial") == "videos"

    @pytest.mark.unit
    def test_explicit_general_is_base_only(self):
        # The per-call way to keep one SearXNG list, even for a query the
        # heuristic would route.
        assert resolve_vertical("arxiv papers on RAG", "general") is None

    @pytest.mark.unit
    def test_heuristic_decides_for_auto_comparison_and_news(self):
        assert resolve_vertical("arxiv papers on RAG", "comparison") == "science"
        assert resolve_vertical("Tavily pricing", "news") is None
        assert resolve_vertical("searxng tutorial", "auto") == "videos"

    @pytest.mark.unit
    def test_explicit_off_and_unknown(self):
        assert resolve_vertical("arxiv papers", None) is None
        assert resolve_vertical("q", "packages") == "packages"
        assert resolve_vertical("q", "bogus") is None


class TestSearXNGConnectorRequests:
    """What the SearXNG connector actually sends."""

    @pytest.mark.unit
    def test_engines_default_is_empty(self):
        assert Settings.model_fields["searxng_engines"].default == ""

    @pytest.mark.unit
    async def test_base_lane_sends_no_engines_param(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))
        connector = SearXNGConnector(host="http://sx.test", engines="")

        result = await connector.search("q", top_k=5)

        assert [c.url.path for c in calls] == ["/search"]  # no capability read for the base lane
        params = calls[0].url.params
        assert "engines" not in params
        assert params["categories"] == "general"
        assert result.connector_name == "searxng"
        assert len(result.sources) == 2
        assert connector.last_unresponsive == [("startpage", "CAPTCHA")]
        assert connector.last_category_status is None

    @pytest.mark.unit
    async def test_vertical_lane_name_category_and_video_metadata(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="videos")

        result = await connector.search("q")

        assert connector.name == "searxng:videos"
        assert _searches(calls)[0].url.params["categories"] == "videos"
        assert result.connector_name == "searxng:videos"
        video = result.sources[0].metadata
        assert video["author"] == "Chan"
        assert video["length"] == "4:29"
        assert "published_date" not in video  # None is not carried

    @pytest.mark.unit
    async def test_per_call_category_override(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))
        connector = SearXNGConnector(host="http://sx.test", engines="")

        await connector.search("q", categories="science")

        assert calls[0].url.params["categories"] == "science"

    @pytest.mark.unit
    async def test_explicit_engines_still_sent_on_the_base_lane(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))
        connector = SearXNGConnector(host="http://sx.test", engines="duckduckgo")

        await connector.search("q")

        assert calls[0].url.params["engines"] == "duckduckgo"


class TestCategoryCapability:
    """Whether the instance defines a category comes from /config, not result labels."""

    @pytest.mark.unit
    async def test_supported_category_is_queried(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://arxiv.org/abs/1", category="science")))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="science")

        result = await connector.search("q")

        assert connector.last_category_status == "supported"
        assert len(_configs(calls)) == 1 and len(_searches(calls)) == 1
        assert [s.url for s in result.sources] == ["https://arxiv.org/abs/1"]

    @pytest.mark.unit
    async def test_missing_category_is_not_queried(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://example.org/x")))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="docs")

        result = await connector.search("q")

        assert connector.last_category_status == "missing"  # only a disabled engine serves it
        assert result.sources == []
        assert _searches(calls) == []

    @pytest.mark.unit
    async def test_secondary_category_labelled_by_primary_is_kept(self, monkeypatch):
        # A stock `packages` engine reports its primary category, `it`.
        config = {"engines": [{"name": "pypi", "categories": ["it", "packages"], "enabled": True}]}
        _mock_http(
            monkeypatch,
            _sx(_results("https://pypi.org/project/httpx/", category="it", engine="pypi"), config=config),
        )
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="packages")

        result = await connector.search("httpx")

        assert connector.last_category_status == "supported"
        assert [s.url for s in result.sources] == ["https://pypi.org/project/httpx/"]

    @pytest.mark.unit
    async def test_unknown_when_config_unreadable(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://example.org/x"), config_status=404))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="science")

        result = await connector.search("q")

        assert connector.last_category_status == "unknown"
        assert len(_searches(calls)) == 1
        assert len(result.sources) == 1

    @pytest.mark.unit
    async def test_capability_is_read_once_per_host(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://arxiv.org/abs/1", category="science")))

        for _ in range(3):
            await SearXNGConnector(host="http://sx.test", engines="", vertical="science").search("q")

        assert len(_configs(calls)) == 1

    @pytest.mark.unit
    async def test_general_vertical_is_checked_like_any_other(self, monkeypatch):
        # A direct `general` search promises general engines too.
        calls = _mock_http(monkeypatch, _sx(_results("https://example.org/x")))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="general")

        result = await connector.search("q")

        assert connector.last_category_status == "supported"
        assert len(_configs(calls)) == 1
        assert [s.url for s in result.sources] == ["https://example.org/x"]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "config",
        [
            {"engines": [{"name": "arxiv", "categories": ["science"]}]},  # no enabled flag
            {"engines": [{"name": "arxiv", "categories": ["science"], "enabled": "yes"}]},
            {"engines": [{"name": "arxiv", "categories": "science", "enabled": True}]},
            {"engines": [{"categories": ["science"], "enabled": True}]},  # no name
            {"engines": [*SX_CONFIG["engines"], "arxiv"]},  # one entry is not an object
            {"engines": []},
            {"engines": "arxiv"},
            ["arxiv"],
        ],
    )
    async def test_a_malformed_config_is_unknown_not_missing(self, monkeypatch, config):
        # A partial reading would report a working category as missing and
        # keep saying so for the whole cache lifetime.
        calls = _mock_http(monkeypatch, _sx(_results("https://arxiv.org/abs/1", category="science"), config=config))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="science")

        await connector.search("q")

        assert connector.last_category_status == "unknown"
        assert len(_searches(calls)) == 1
        expiry, cached = searxng_mod._capabilities["http://sx.test"]
        assert cached is None
        assert expiry - time.monotonic() <= searxng_mod.CAPABILITY_RETRY_SECONDS


class TestCapabilityReads:
    """One /config read per host, however many searches ask at once."""

    HOST = "http://sx.test"

    @staticmethod
    def _held(monkeypatch, respond):
        """Answer /config with `respond()` once the returned event is set."""
        release = asyncio.Event()

        async def handler(request):
            await release.wait()
            return respond()

        return _mock_http(monkeypatch, handler), release

    async def _ask_together(self, release, n=5):
        pending = [asyncio.ensure_future(searxng_mod.instance_categories(self.HOST)) for _ in range(n)]
        await asyncio.sleep(0.05)  # every caller is now waiting on the read
        release.set()
        return await asyncio.gather(*pending)

    @pytest.mark.unit
    async def test_concurrent_callers_share_one_read(self, monkeypatch):
        calls, release = self._held(monkeypatch, lambda: httpx.Response(200, json=SX_CONFIG))

        answers = await self._ask_together(release)

        assert len(calls) == 1
        assert all(answer == answers[0] for answer in answers)
        assert answers[0]["science"] == frozenset({"arxiv"})
        assert "docs" not in answers[0]  # served by a disabled engine only

    @pytest.mark.unit
    async def test_concurrent_callers_share_one_failed_read(self, monkeypatch):
        calls, release = self._held(monkeypatch, lambda: httpx.Response(503))

        answers = await self._ask_together(release)

        assert answers == [None] * 5
        assert len(calls) == 1
        # The failure is cached for the retry interval, not re-read per call.
        assert await searxng_mod.instance_categories(self.HOST) is None
        assert len(calls) == 1

    @pytest.mark.unit
    async def test_one_caller_giving_up_does_not_cancel_the_read(self, monkeypatch):
        calls, release = self._held(monkeypatch, lambda: httpx.Response(200, json=SX_CONFIG))
        first = asyncio.ensure_future(searxng_mod.instance_categories(self.HOST))
        second = asyncio.ensure_future(searxng_mod.instance_categories(self.HOST))
        await asyncio.sleep(0.05)

        first.cancel()
        release.set()

        assert (await second)["science"] == frozenset({"arxiv"})
        assert len(calls) == 1

    @pytest.mark.unit
    async def test_a_failed_read_never_replaces_a_fresh_one(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(503))
        good = {"science": frozenset({"arxiv"})}
        searxng_mod._capabilities[self.HOST] = (time.monotonic() + 600, good)
        searxng_mod._latest_read[self.HOST] = 7

        assert await searxng_mod._read_capabilities(self.HOST, 7) == good
        assert searxng_mod._capabilities[self.HOST][1] == good

    @pytest.mark.unit
    async def test_an_older_read_never_replaces_a_newer_one(self, monkeypatch):
        # A read in flight on another event loop is not shared, so this loop
        # starts its own. The older read finishes last and must not win.
        older_config = {"engines": [{"name": "arxiv", "categories": ["science"], "enabled": True}]}
        answers = [older_config, SX_CONFIG]  # by request order
        gates = [threading.Event(), threading.Event()]
        started: list[int] = []

        async def handler(request):
            index = len(started)
            started.append(index)
            while not gates[index].is_set():
                await asyncio.sleep(0.005)
            return httpx.Response(200, json=answers[index])

        async def until(condition):
            for _ in range(400):
                if condition():
                    return
                await asyncio.sleep(0.005)
            raise AssertionError("timed out")

        _mock_http(monkeypatch, handler)
        other = asyncio.new_event_loop()
        thread = threading.Thread(target=other.run_forever, daemon=True)
        thread.start()
        try:
            older = asyncio.run_coroutine_threadsafe(searxng_mod.instance_categories(self.HOST), other)
            await until(lambda: len(started) == 1)
            newer = asyncio.ensure_future(searxng_mod.instance_categories(self.HOST))
            await until(lambda: len(started) == 2)

            gates[1].set()
            assert (await newer)["it"] == frozenset({"stackoverflow"})
            gates[0].set()
            assert "it" not in await asyncio.wrap_future(older)
        finally:
            other.call_soon_threadsafe(other.stop)
            thread.join(timeout=5)
            other.close()

        assert searxng_mod._capabilities[self.HOST][1]["it"] == frozenset({"stackoverflow"})

    @pytest.mark.unit
    async def test_a_newer_read_cannot_slip_between_check_and_write(self, monkeypatch):
        # Once a read has confirmed it is still the latest, a read on another
        # thread must not register, fetch and write before that read's own
        # write lands on top of it: the cache must hold the last fetched answer.
        answers = [
            {"engines": [{"name": "arxiv", "categories": ["science"], "enabled": True}]},
            SX_CONFIG,
        ]
        fetched: list[dict] = []

        def handler(request):
            answer = answers[len(fetched)]
            fetched.append(answer)
            return httpx.Response(200, json=answer)

        _mock_http(monkeypatch, handler)
        host = self.HOST
        workers: list[threading.Thread] = []

        class _CheckHook(dict):
            """Runs a whole read on another thread at the first "am I latest?" check."""

            def get(self, *args):
                value = super().get(*args)
                if not workers:
                    worker = threading.Thread(target=lambda: asyncio.run(searxng_mod.instance_categories(host)))
                    workers.append(worker)
                    worker.start()
                    worker.join(timeout=0.5)  # finishes here unless the check holds the lock
                return value

        monkeypatch.setattr(searxng_mod, "_latest_read", _CheckHook())

        await searxng_mod.instance_categories(host)
        workers[0].join(timeout=5)

        assert not workers[0].is_alive()
        assert searxng_mod._capabilities[host][1] == searxng_mod._category_engines(fetched[-1])


def _result(url, engine, category, engines=None):
    result = {"url": url, "title": url, "content": url, "engine": engine, "category": category}
    if engines is not None:
        result["engines"] = engines
    return result


class TestHonouredSelection:
    """A defined category is not proof the request got it: keep what its engines returned."""

    @staticmethod
    def _science():
        return SearXNGConnector(host="http://sx.test", engines="", vertical="science")

    @pytest.mark.unit
    async def test_a_locked_category_answer_is_dropped(self, monkeypatch):
        # With `categories` locked, the instance answers from its locked
        # (general) engines whatever the request asks for.
        _mock_http(monkeypatch, _sx(_results("https://g.example/1", "https://g.example/2")))
        connector = self._science()

        result = await connector.search("q")

        assert connector.last_category_status == "supported"
        assert result.sources == []
        assert connector.last_off_category == 2

    @pytest.mark.unit
    async def test_a_bang_answer_is_dropped_from_general_too(self, monkeypatch):
        _mock_http(monkeypatch, _sx(_results("https://arxiv.org/abs/1", category="science")))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="general")

        result = await connector.search("!arxiv speculative decoding")

        assert result.sources == []
        assert connector.last_off_category == 1

    @pytest.mark.unit
    async def test_a_bang_answer_is_dropped(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://ddg.example/1")))
        connector = SearXNGConnector(host="http://sx.test", engines="", vertical="videos")

        result = await connector.search("!ddg rust async")

        assert _searches(calls)[0].url.params["q"] == "!ddg rust async"
        assert result.sources == []
        assert connector.last_off_category == 1

    @pytest.mark.unit
    async def test_kept_results_keep_their_positions(self, monkeypatch):
        payload = {
            "results": [
                _result("https://g.example/1", "duckduckgo", "general"),
                _result("https://arxiv.org/abs/2", "arxiv", "science"),
                # Found by a general engine first, and by arXiv too.
                _result("https://arxiv.org/abs/3", "duckduckgo", "general", engines=["duckduckgo", "arxiv"]),
            ]
        }
        _mock_http(monkeypatch, _sx(payload))
        connector = self._science()

        result = await connector.search("q")

        assert [(s.url, s.metadata["position"]) for s in result.sources] == [
            ("https://arxiv.org/abs/2", 2),
            ("https://arxiv.org/abs/3", 3),
        ]
        assert connector.last_off_category == 1

    @pytest.mark.unit
    async def test_top_k_counts_kept_results(self, monkeypatch):
        general = [_result(f"https://g.example/{i}", "duckduckgo", "general") for i in range(3)]
        science = [_result(f"https://arxiv.org/abs/{i}", "arxiv", "science") for i in range(3)]
        _mock_http(monkeypatch, _sx({"results": general + science}))

        result = await self._science().search("q", top_k=2)

        assert [s.metadata["position"] for s in result.sources] == [4, 5]

    @pytest.mark.unit
    async def test_the_base_lane_is_not_filtered(self, monkeypatch):
        _mock_http(monkeypatch, _sx(_results("https://arxiv.org/abs/1", category="science")))
        connector = SearXNGConnector(host="http://sx.test", engines="")

        result = await connector.search("q")

        assert len(result.sources) == 1
        assert connector.last_off_category == 0


class _StubConnector(Connector):
    """A configured non-SearXNG connector returning one fixed source."""

    name = "stub"

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        source = Source(id="st_1", title="S", url="https://stub.example", content="s", connector=self.name)
        return SearchResult(sources=[source], query=query, connector_name=self.name)


class TestAggregatorVerticalLane:
    """The aggregator adds one vertical list, fused beside the base lane."""

    @staticmethod
    def _aggregator(vertical, engines=""):
        return SearchAggregator(
            connectors=[SearXNGConnector(host="http://sx.test", engines=engines)],
            vertical=vertical,
        )

    @pytest.mark.unit
    async def test_adds_lane_when_routed_and_supported(self, monkeypatch):
        by_category = {"general": _results("https://g.example/1"), "videos": SX_PAYLOAD}
        calls = _mock_http(monkeypatch, _sx(None, by_category=by_category))
        aggregator = self._aggregator("tutorial")

        fused, raw = await aggregator.search("anything", top_k=5)

        assert set(raw) == {"searxng", "searxng:videos"}
        assert sorted(c.url.params["categories"] for c in _searches(calls)) == ["general", "videos"]
        assert aggregator.get_active_connectors() == ["searxng"]
        assert fused

    @pytest.mark.unit
    async def test_no_lane_when_category_missing(self, monkeypatch):
        config = {"engines": [{"name": "duckduckgo", "categories": ["general"], "enabled": True}]}
        calls = _mock_http(monkeypatch, _sx(_results("https://g.example/1"), config=config))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}
        assert [c.url.params["categories"] for c in _searches(calls)] == ["general"]

    @pytest.mark.unit
    async def test_no_lane_when_capability_unknown(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://g.example/1"), config_status=500))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}
        assert len(_searches(calls)) == 1

    @pytest.mark.unit
    async def test_no_lane_when_routing_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_vertical_routing", False)
        _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}

    @pytest.mark.unit
    async def test_no_lane_without_vertical_policy(self, monkeypatch):
        _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        _, raw = await self._aggregator(None).search("recent arxiv papers", top_k=5)

        assert set(raw) == {"searxng"}

    @pytest.mark.unit
    async def test_no_lane_for_explicit_general(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        _, raw = await self._aggregator("general").search("recent arxiv papers", top_k=5)

        assert set(raw) == {"searxng"}
        assert _configs(calls) == []

    @pytest.mark.unit
    async def test_no_lane_when_searxng_not_active(self):
        aggregator = SearchAggregator(connectors=[_StubConnector()], vertical="academic")

        _, raw = await aggregator.search("anything", top_k=5)

        assert set(raw) == {"stub"}

    @pytest.mark.unit
    async def test_lane_ignores_the_engine_pin(self, monkeypatch):
        # SearXNG adds named engines to the requested category, so a pinned
        # lane would carry general results.
        by_category = {"general": _results("https://g.example/1"), "science": _results("https://s.example/1", category="science")}
        calls = _mock_http(monkeypatch, _sx(None, by_category=by_category))

        await self._aggregator("academic", engines="duckduckgo,brave").search("anything", top_k=5)

        by_cat = {c.url.params["categories"]: c.url.params for c in _searches(calls)}
        assert by_cat["general"]["engines"] == "duckduckgo,brave"
        assert "engines" not in by_cat["science"]

    @pytest.mark.unit
    async def test_a_url_in_both_lists_is_counted_once(self, monkeypatch):
        shared, extra = "https://shared.example/paper", "https://arxiv.org/abs/2"
        by_category = {
            "general": _results(shared),
            "science": _results(shared, extra, category="science"),
        }
        _mock_http(monkeypatch, _sx(None, by_category=by_category))

        fused, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert [s.url for s in raw["searxng:science"].sources] == [extra]
        scores = {s.url: s.score for s in fused}
        # Rank 1 in the base list only — not boosted by the lane's copy.
        assert scores[shared] == pytest.approx(1.0 / (settings.rrf_k + 1))
        assert extra in scores

    @pytest.mark.unit
    async def test_a_lane_of_only_duplicates_is_dropped(self, monkeypatch):
        urls = ("https://g.example/1", "https://g.example/2")
        by_category = {"general": _results(*urls), "science": _results(*urls, category="science")}
        _mock_http(monkeypatch, _sx(None, by_category=by_category))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}

    @pytest.mark.unit
    async def test_a_locked_category_adds_no_lane(self, monkeypatch):
        # Both requests are answered by general engines. The lane's URL is not
        # in the base list, so only the engine check keeps it out.
        by_category = {"general": _results("https://g.example/1"), "science": _results("https://g.example/2")}
        _mock_http(monkeypatch, _sx(None, by_category=by_category))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}

    @pytest.mark.unit
    async def test_a_duplicate_does_not_promote_the_next_lane_result(self, monkeypatch):
        shared, unique = "https://shared.example/paper", "https://arxiv.org/abs/2"
        by_category = {
            "general": _results(shared),
            "science": _results(shared, unique, category="science"),
        }
        _mock_http(monkeypatch, _sx(None, by_category=by_category))

        fused, _ = await self._aggregator("academic").search("anything", top_k=5)

        # Rank 2 in the lane, not rank 1.
        assert {s.url: s.score for s in fused}[unique] == pytest.approx(1.0 / (settings.rrf_k + 2))

    @pytest.mark.unit
    async def test_an_off_category_result_does_not_promote_the_next(self, monkeypatch):
        science = {
            "results": [
                _result("https://g.example/x", "duckduckgo", "general"),
                _result("https://arxiv.org/abs/2", "arxiv", "science"),
            ]
        }
        by_category = {"general": _results("https://g.example/1"), "science": science}
        _mock_http(monkeypatch, _sx(None, by_category=by_category))

        fused, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert [s.url for s in raw["searxng:science"].sources] == ["https://arxiv.org/abs/2"]
        scores = {s.url: s.score for s in fused}
        assert "https://g.example/x" not in scores
        assert scores["https://arxiv.org/abs/2"] == pytest.approx(1.0 / (settings.rrf_k + 2))

    @pytest.mark.unit
    async def test_a_slow_config_read_does_not_hold_back_the_providers(self, monkeypatch):
        provider_started = asyncio.Event()

        class _Provider(_StubConnector):
            async def search(self, query, top_k=10):
                provider_started.set()
                return await super().search(query, top_k)

        async def handler(request):
            if request.url.path == "/config":
                # Answers only once a provider search is under way.
                await asyncio.wait_for(provider_started.wait(), timeout=2)
                return httpx.Response(200, json=SX_CONFIG)
            category = request.url.params["categories"]
            return httpx.Response(200, json=_results(f"https://{category}.example/1", category=category))

        _mock_http(monkeypatch, handler)
        aggregator = SearchAggregator(
            connectors=[SearXNGConnector(host="http://sx.test", engines=""), _Provider()],
            vertical="academic",
        )

        _, raw = await aggregator.search("anything", top_k=5)

        assert set(raw) == {"searxng", "searxng:science", "stub"}

    @pytest.mark.unit
    async def test_a_lane_the_connector_cannot_confirm_is_dropped(self, monkeypatch):
        # The aggregator saw the category, but the connector's own check comes
        # back unknown (the cached map expired and the re-read failed).
        from src.search import aggregator as aggregator_mod

        async def confirmed(host):
            return {"science": frozenset({"arxiv"})}

        monkeypatch.setattr(aggregator_mod, "instance_categories", confirmed)
        by_category = {"general": _results("https://g.example/1"), "science": _results("https://g.example/2")}
        _mock_http(monkeypatch, _sx(None, config_status=500, by_category=by_category))

        _, raw = await self._aggregator("academic").search("anything", top_k=5)

        assert set(raw) == {"searxng"}


class TestParallelConnector:
    """Request shape, parsing and the no-credit backoff."""

    @pytest.mark.unit
    def test_not_configured_without_key(self, monkeypatch):
        monkeypatch.setattr(settings, "parallel_api_key", "")
        assert ParallelConnector(api_key="").is_configured() is False

    @pytest.mark.unit
    async def test_request_shape_and_parsing(self, monkeypatch):
        calls = _mock_http(monkeypatch, lambda r: httpx.Response(200, json=PX_PAYLOAD))
        connector = ParallelConnector(api_key="k-test", mode="fast", max_results=10, excerpt_chars=900)

        result = await connector.search("what is rrf fusion", top_k=25)

        request = calls[0]
        assert request.method == "POST"
        assert str(request.url) == "https://api.parallel.ai/v1/search"
        assert request.headers["x-api-key"] == "k-test"
        body = json.loads(request.content)
        assert body["mode"] == "fast"
        assert body["objective"] == "what is rrf fusion"
        assert body["search_queries"] == ["what is rrf fusion"]
        assert body["advanced_settings"]["max_results"] == 10  # capped below top_k
        assert body["advanced_settings"]["excerpt_settings"]["max_chars_per_result"] == 900

        assert [s.url for s in result.sources] == ["https://a.example/1", "https://a.example/2"]
        assert result.sources[0].content == "one\n\ntwo"
        assert result.sources[0].metadata["published_date"] == "2026-09-01"
        assert result.sources[0].id.startswith("px_")
        assert result.sources[1].title == ""
        assert result.connector_name == "parallel"

    @pytest.mark.unit
    async def test_unknown_mode_falls_back_to_fast(self, monkeypatch):
        calls = _mock_http(monkeypatch, lambda r: httpx.Response(200, json=PX_PAYLOAD))

        await ParallelConnector(api_key="k", mode="deep").search("q")

        assert json.loads(calls[0].content)["mode"] == "fast"

    @pytest.mark.unit
    async def test_402_opens_backoff(self, monkeypatch):
        error = {"type": "error", "error": {"ref_id": "r", "message": "Insufficient credit in account"}}
        calls = _mock_http(monkeypatch, lambda r: httpx.Response(402, json=error))
        connector = ParallelConnector(api_key="k")

        first = await connector.search("q")
        second = await connector.search("q")

        assert first.sources == [] and second.sources == []
        assert len(calls) == 1  # the second call never left the process

    @pytest.mark.unit
    async def test_backoff_is_per_account(self, monkeypatch):
        def handler(request):
            if request.headers["x-api-key"] == "broke":
                return httpx.Response(402, json={})
            return httpx.Response(200, json=PX_PAYLOAD)

        calls = _mock_http(monkeypatch, handler)

        await ParallelConnector(api_key="broke").search("q")
        funded = await ParallelConnector(api_key="funded").search("q")

        assert len(funded.sources) == 2
        assert [c.headers["x-api-key"] for c in calls] == ["broke", "funded"]

    @pytest.mark.unit
    async def test_server_error_does_not_back_off(self, monkeypatch):
        calls = _mock_http(monkeypatch, lambda r: httpx.Response(500, text="boom"))
        connector = ParallelConnector(api_key="k")

        await connector.search("q")
        await connector.search("q")

        assert len(calls) == 2


class TestVerticalSearchTool:
    """The vertical_search MCP tool output."""

    @pytest.fixture(autouse=True)
    def _instance(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_host", "http://sx.test")
        monkeypatch.setattr(settings, "searxng_engines", "")

    @pytest.mark.unit
    async def test_formats_video_results_and_flags_unresponsive(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        out = await mcp_server.vertical_search(query="searxng setup", vertical="videos", top_k=5)

        assert _searches(calls)[0].url.params["categories"] == "videos"
        assert "https://www.youtube.com/watch?v=abc" in out
        assert "channel: Chan" in out
        assert "length: 4:29" in out
        assert "Unresponsive engines: startpage (CAPTCHA)" in out
        assert "https://example.org/b" not in out  # a duckduckgo (general) result
        assert "Off-category results dropped: 1" in out

    @pytest.mark.unit
    async def test_ignores_the_engine_pin(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_engines", "duckduckgo")
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        await mcp_server.vertical_search(query="q", vertical="videos")

        assert "engines" not in _searches(calls)[0].url.params

    @pytest.mark.unit
    async def test_reports_a_missing_category(self, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://example.org/x")))

        out = await mcp_server.vertical_search(query="q", vertical="docs")

        assert "no enabled engine in category `docs`" in out
        assert _searches(calls) == []

    @pytest.mark.unit
    async def test_reports_unverified_capability(self, monkeypatch):
        _mock_http(monkeypatch, _sx(_results("https://example.org/x"), config_status=404))

        out = await mcp_server.vertical_search(query="q", vertical="science")

        assert "Could not read the instance's /config" in out
        assert "https://example.org/x" in out

    @pytest.mark.unit
    async def test_unconfigured_host(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_host", "")

        out = await mcp_server.vertical_search(query="q", vertical="science")

        assert "not configured" in out


class _Stop(Exception):
    """Raised by a spy once it has recorded what the caller passed."""


class _SpyAggregator:
    """Stands in for SearchAggregator: records the lane policy, finds nothing."""

    instances: list["_SpyAggregator"] = []

    def __init__(self, connectors=None, top_k=None, vertical=None):
        self.vertical = vertical
        self.connectors = [_Named(n) for n in ("searxng", "tavily", "parallel")]
        _SpyAggregator.instances.append(self)

    async def search(self, query, top_k=None, connectors=None, connector_weights=None):
        return [], {}

    def get_active_connectors(self):
        return [c.name for c in self.connectors]


class _Named:
    def __init__(self, name):
        self.name = name


class TestFocusModePropagation:
    """Each tool hands its focus mode to the aggregator as the lane policy."""

    @pytest.fixture(autouse=True)
    def _spy(self, monkeypatch):
        _SpyAggregator.instances = []
        monkeypatch.setattr(mcp_server, "SearchAggregator", _SpyAggregator)
        monkeypatch.setattr(mcp_server, "_get_llm_client", lambda *a, **k: object())

    @pytest.mark.unit
    @pytest.mark.parametrize("focus_mode,policy", [(None, "auto"), ("general", "general"), ("academic", "academic")])
    async def test_mcp_search(self, focus_mode, policy):
        await mcp_server.search(query="q", focus_mode=focus_mode)
        assert _SpyAggregator.instances[-1].vertical == policy

    @pytest.mark.unit
    async def test_mcp_research_uses_the_heuristic(self):
        await mcp_server.research(query="q")
        assert _SpyAggregator.instances[-1].vertical == "auto"

    @pytest.mark.unit
    @pytest.mark.parametrize("focus_mode,policy", [(None, "auto"), ("general", "general"), ("tutorial", "tutorial")])
    async def test_mcp_discover(self, monkeypatch, focus_mode, policy):
        class _Explorer:
            def __init__(self, *a, **k):
                raise _Stop

        monkeypatch.setattr(mcp_server, "Explorer", _Explorer)
        with pytest.raises(_Stop):
            await mcp_server.discover(query="q", focus_mode=focus_mode)
        assert _SpyAggregator.instances[-1].vertical == policy


class TestRestSurface:
    """REST parity: /vertical-search, and the lane on /search, /research, /discover."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.api import routes as routes_mod
        from src.main import app

        monkeypatch.setattr(routes_mod.cache, "get", lambda *a, **k: None)
        monkeypatch.setattr(routes_mod.cache, "set", lambda *a, **k: None)
        monkeypatch.setattr(settings, "searxng_host", "http://sx.test")
        monkeypatch.setattr(settings, "searxng_engines", "")
        for key in ("tavily_api_key", "linkup_api_key", "brave_api_key", "parallel_api_key"):
            monkeypatch.setattr(settings, key, "")
        return TestClient(app)

    @pytest.fixture
    def spy(self, monkeypatch):
        from src.api import routes as routes_mod

        _SpyAggregator.instances = []
        monkeypatch.setattr(routes_mod, "SearchAggregator", _SpyAggregator)
        return routes_mod

    @pytest.mark.unit
    def test_vertical_search_endpoint_shape(self, client, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(SX_PAYLOAD))

        resp = client.post("/api/v1/vertical-search", json={"query": "searxng setup", "vertical": "videos", "top_k": 5})

        assert resp.status_code == 200
        body = resp.json()
        search = _searches(calls)[0]
        assert search.url.params["categories"] == "videos"
        assert "engines" not in search.url.params
        assert body["vertical"] == "videos"
        # The duckduckgo result is not a videos-engine answer.
        assert body["total_results"] == 1
        assert body["off_category_results"] == 1
        first = body["sources"][0]
        assert first["connector"] == "searxng:videos"
        assert first["engine"] == "youtube"
        assert first["author"] == "Chan"
        assert first["length"] == "4:29"
        assert first["published_date"] is None
        assert body["unresponsive_engines"] == [{"engine": "startpage", "reason": "CAPTCHA"}]
        assert body["category_status"] == "supported"

    @pytest.mark.unit
    def test_vertical_search_endpoint_reports_missing_category(self, client, monkeypatch):
        calls = _mock_http(monkeypatch, _sx(_results("https://example.org/x")))

        body = client.post("/api/v1/vertical-search", json={"query": "q", "vertical": "docs"}).json()

        assert body["category_status"] == "missing"
        assert body["sources"] == []
        assert _searches(calls) == []

    @pytest.mark.unit
    def test_vertical_search_endpoint_unconfigured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "searxng_host", "")

        resp = client.post("/api/v1/vertical-search", json={"query": "q"})

        assert resp.status_code == 503

    @pytest.mark.unit
    def test_search_endpoint_adds_the_focus_mode_lane(self, client, monkeypatch):
        by_category = {"general": _results("https://g.example/1"), "videos": SX_PAYLOAD}
        calls = _mock_http(monkeypatch, _sx(None, by_category=by_category))

        body = client.post("/api/v1/search", json={"query": "anything", "focus_mode": "tutorial"}).json()

        assert sorted(c.url.params["categories"] for c in _searches(calls)) == ["general", "videos"]
        assert set(body["connectors_used"]) == {"searxng", "searxng:videos"}

    @pytest.mark.unit
    def test_research_endpoint_passes_its_focus_mode(self, client, spy):
        client.post("/api/v1/research", json={"query": "q", "focus_mode": "academic"})
        client.post("/api/v1/research", json={"query": "q"})

        assert [a.vertical for a in _SpyAggregator.instances] == ["academic", "auto"]

    @pytest.mark.unit
    @pytest.mark.parametrize("extra", [{}, {"fill_gaps": False}])
    def test_discover_endpoint_takes_focus_mode_and_connector_filter(self, client, spy, monkeypatch, extra):
        def _stop(*a, **k):
            raise _Stop

        monkeypatch.setattr(spy, "_get_llm_client", _stop)
        body = {"query": "q", "focus_mode": "debugging", "connectors": ["searxng", "tavily"], **extra}

        with pytest.raises(_Stop):
            client.post("/api/v1/discover", json=body)

        aggregator = _SpyAggregator.instances[-1]
        assert aggregator.vertical == "debugging"
        assert [c.name for c in aggregator.connectors] == ["searxng", "tavily"]

    @pytest.mark.unit
    @pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/research", "/api/v1/discover"])
    @pytest.mark.parametrize("connectors", [["*"], ["searxng,tavily"], ["searxng:science"], ["bogus"]])
    def test_unknown_connector_names_are_rejected(self, client, spy, path, connectors):
        # Unvalidated, `["*"]` matched no connector and cached an empty result
        # under the key of an unfiltered request.
        resp = client.post(path, json={"query": "q", "connectors": connectors})

        assert resp.status_code == 422
        assert _SpyAggregator.instances == []

    @pytest.mark.unit
    def test_accepted_names_are_the_known_connectors(self):
        from typing import get_args

        from src.api.schemas import ConnectorName
        from src.connectors.doctor import known_connectors

        assert set(get_args(ConnectorName)) == {c.name for c in known_connectors()}


class TestCacheKeys:
    """REST cache keys separate every behaviour-affecting input."""

    @pytest.mark.unit
    def test_search_key(self):
        from src.cache import build_search_cache_extra

        def key(**overrides):
            args = {"top_k": 10, "connectors": None, "focus_mode": None, "vertical_routing": True}
            args.update(overrides)
            return build_search_cache_extra(**args)

        assert key() != key(focus_mode="academic")
        assert key() != key(connectors=["tavily"])
        assert key() != key(vertical_routing=False)
        assert key() != key(top_k=5)
        # The aggregator matches the filter as a set, so order and repeats do not matter.
        assert key(connectors=["tavily", "searxng"]) == key(connectors=["searxng", "tavily", "tavily"])
        assert key(connectors=[]) == key(connectors=None)

    @pytest.mark.unit
    def test_discover_key_carries_connectors_and_routing(self):
        from src.cache import build_discover_cache_extra

        def key(**overrides):
            args = {
                "model": "m", "top_k": 10, "expand_searches": True, "fill_gaps": True,
                "use_adaptive_routing": True, "focus_mode": None, "identify_gaps": True,
            }
            args.update(overrides)
            return build_discover_cache_extra(**args)

        assert key() != key(connectors=["tavily"])
        assert key() != key(vertical_routing=False)
        assert key(connectors=["tavily", "searxng"]) == key(connectors=["searxng", "tavily"])

    @pytest.mark.unit
    def test_connector_filters_never_share_a_key(self):
        from src.cache import build_discover_cache_extra, build_search_cache_extra

        builders = (
            (build_search_cache_extra, {"top_k": 10, "focus_mode": None, "vertical_routing": True}),
            (
                build_discover_cache_extra,
                {
                    "model": "m", "top_k": 10, "expand_searches": True, "fill_gaps": True,
                    "use_adaptive_routing": True, "focus_mode": None, "identify_gaps": True,
                },
            ),
        )
        for build, args in builders:
            def key(connectors):
                return build(**args, connectors=connectors)

            assert key(["*"]) != key(None)
            assert key(["searxng,tavily"]) != key(["searxng", "tavily"])
            assert key([]) == key(None)  # both mean every connector


def _enum_values(schema) -> set:
    """Every enum value anywhere inside a JSON-schema fragment."""
    found: set = set()
    if isinstance(schema, dict):
        found.update(schema.get("enum") or [])
        for value in schema.values():
            found |= _enum_values(value)
    elif isinstance(schema, list):
        for value in schema:
            found |= _enum_values(value)
    return found


class TestSurfaceParity:
    """The HTTP MCP surface exposes exactly the stdio MCP tool set, with matching inputs."""

    @staticmethod
    async def _surfaces():
        from src.main import mcp as http_mcp
        from src.mcp_server import mcp as stdio_mcp

        stdio = {t.name: t.parameters for t in await stdio_mcp.list_tools()}
        http = {t.name.split("_api_v1_")[0]: t.inputSchema for t in http_mcp.tools}
        return stdio, http

    @pytest.mark.unit
    async def test_tool_sets_match(self):
        stdio, http = await self._surfaces()

        assert set(stdio) == set(http) == {
            "search", "vertical_search", "research", "ask", "discover", "synthesize", "reason",
        }

    @pytest.mark.unit
    async def test_search_and_vertical_search_inputs_match(self):
        stdio, http = await self._surfaces()

        for tool, shared in (("search", {"query", "top_k", "focus_mode"}), ("vertical_search", {"query", "vertical", "top_k"})):
            assert shared <= set(stdio[tool]["properties"])
            assert shared <= set(http[tool]["properties"])
        for tool, field in (("search", "focus_mode"), ("vertical_search", "vertical"), ("discover", "focus_mode")):
            stdio_enum = _enum_values(stdio[tool]["properties"][field])
            http_enum = _enum_values(http[tool]["properties"][field])
            assert stdio_enum and stdio_enum == http_enum, (tool, field)

    @pytest.mark.unit
    async def test_every_http_tool_advertises_its_query(self):
        # A union request body used to leave `discover` with an empty schema.
        _, http = await self._surfaces()

        assert {tool for tool, schema in http.items() if "query" not in schema.get("properties", {})} == set()
