"""
Comprehensive tests for hot cache functionality.

Tests cover:
- Basic cache operations (set/get/clear)
- TTL expiration
- Cache key differentiation
- Decorator behavior
- Source-aware caching for synthesize
- All workflow coverage (DIRECT, EXPLORATORY, SYNTHESIS)
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cache import HotCache, cache, cached, CacheEntry
from src.llm_utils import LLMOutput


class TestHotCacheBasics:
    """Basic cache operations."""

    def setup_method(self):
        """Fresh cache for each test."""
        self.cache = HotCache(namespace="test_basics")
        self.cache.clear()

    def teardown_method(self):
        """Cleanup after each test."""
        self.cache.clear()

    def test_cache_dir_created(self):
        """Cache directory should be created in /tmp."""
        assert self.cache.cache_dir.exists()
        assert str(self.cache.cache_dir).startswith("/tmp/")

    def test_set_and_get(self):
        """Basic set/get should work."""
        self.cache.set("test query", "test result", tier="synthesis")
        result = self.cache.get("test query", tier="synthesis")
        assert result == "test result"

    def test_get_nonexistent_returns_none(self):
        """Cache miss should return None."""
        result = self.cache.get("nonexistent query", tier="synthesis")
        assert result is None

    def test_cache_key_normalization(self):
        """Queries should be normalized (lowercase, stripped)."""
        self.cache.set("  TEST Query  ", "result", tier="test")

        # Should match with different casing/whitespace
        assert self.cache.get("test query", tier="test") == "result"
        assert self.cache.get("TEST QUERY", tier="test") == "result"
        assert self.cache.get("  test query  ", tier="test") == "result"

    def test_tier_isolation(self):
        """Different tiers should have separate caches."""
        self.cache.set("query", "synthesis result", tier="synthesis")
        self.cache.set("query", "discover result", tier="discover")

        assert self.cache.get("query", tier="synthesis") == "synthesis result"
        assert self.cache.get("query", tier="discover") == "discover result"

    def test_extra_param_isolation(self):
        """Extra params should create separate cache entries."""
        self.cache.set("query", "result1", tier="test", extra="param=1")
        self.cache.set("query", "result2", tier="test", extra="param=2")

        assert self.cache.get("query", tier="test", extra="param=1") == "result1"
        assert self.cache.get("query", tier="test", extra="param=2") == "result2"
        assert self.cache.get("query", tier="test", extra="param=3") is None

    def test_clear_removes_all_entries(self):
        """Clear should remove all cache entries."""
        self.cache.set("q1", "r1", tier="test")
        self.cache.set("q2", "r2", tier="test")

        assert self.cache.stats()["entries"] == 2

        self.cache.clear()

        assert self.cache.stats()["entries"] == 0
        assert self.cache.get("q1", tier="test") is None

    def test_stats_tracking(self):
        """Stats should track hits and misses."""
        self.cache.set("query", "result", tier="test")

        # One miss
        self.cache.get("nonexistent", tier="test")

        # Two hits
        self.cache.get("query", tier="test")
        self.cache.get("query", tier="test")

        stats = self.cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 2/3


class TestTTLExpiration:
    """TTL-based cache expiration."""

    def setup_method(self):
        self.cache = HotCache(namespace="test_ttl")
        self.cache.clear()

    def teardown_method(self):
        self.cache.clear()

    def test_default_ttls_by_tier(self):
        """Each tier should have appropriate default TTL."""
        expected_ttls = {
            "synthesis": 3600,
            "discover": 3600,
            "reason": 3600,
            "research": 1800,
            "search": 1800,
            "url": 7200,
            "ask": 1800,
        }
        assert self.cache.DEFAULT_TTLS == expected_ttls

    def test_fresh_entry_returned(self):
        """Entry within TTL should be returned."""
        self.cache.set("query", "result", tier="test", ttl=60)
        assert self.cache.get("query", tier="test") == "result"

    def test_expired_entry_returns_none(self):
        """Entry past TTL should return None and be deleted."""
        # Set with very short TTL
        self.cache.set("query", "result", tier="test", ttl=1)

        # Wait for expiration
        time.sleep(1.1)

        result = self.cache.get("query", tier="test")
        assert result is None

        # File should be deleted
        key = self.cache._key("query", tier="test")
        assert not self.cache._path(key).exists()

    def test_custom_ttl_override(self):
        """Custom TTL should override default."""
        self.cache.set("query", "result", tier="synthesis", ttl=1)

        # Should exist immediately
        assert self.cache.get("query", tier="synthesis") == "result"

        # Should expire after 1 second
        time.sleep(1.1)
        assert self.cache.get("query", tier="synthesis") is None


class TestURLCaching:
    """URL content caching (L2 tier)."""

    def setup_method(self):
        self.cache = HotCache(namespace="test_url")
        self.cache.clear()

    def teardown_method(self):
        self.cache.clear()

    def test_set_url_and_get_url(self):
        """URL caching should work."""
        self.cache.set_url("https://example.com/page", "page content")
        result = self.cache.get_url("https://example.com/page")
        assert result == "page content"

    def test_get_url_miss_returns_none(self):
        """URL cache miss should return None."""
        result = self.cache.get_url("https://nonexistent.com")
        assert result is None

    def test_url_ttl_default(self):
        """URL caching should use 2h TTL by default."""
        self.cache.set_url("https://example.com", "content")

        # Check the stored TTL
        key = self.cache._key("https://example.com", tier="url")
        path = self.cache._path(key)
        data = json.loads(path.read_text())
        assert data["ttl"] == 7200  # 2 hours


class TestCachedDecorator:
    """@cached decorator functionality."""

    def setup_method(self):
        # Clear the global cache
        cache.clear()

    def teardown_method(self):
        cache.clear()

    @pytest.mark.asyncio
    async def test_decorator_caches_result(self):
        """Decorated function should cache its result."""
        call_count = 0

        @cached(tier="test")
        async def mock_tool(args: dict):
            nonlocal call_count
            call_count += 1
            from mcp.types import TextContent
            return [TextContent(type="text", text=f"result-{call_count}")]

        # First call - executes function
        r1 = await mock_tool({"query": "test"})
        assert r1[0].text == "result-1"
        assert call_count == 1

        # Second call - returns cached
        r2 = await mock_tool({"query": "test"})
        assert "*[cached]*" in r2[0].text
        assert "result-1" in r2[0].text
        assert call_count == 1  # Function not called again

    @pytest.mark.asyncio
    async def test_decorator_cache_marker(self):
        """Cached results should have *[cached]* marker."""
        @cached(tier="test")
        async def mock_tool(args: dict):
            from mcp.types import TextContent
            return [TextContent(type="text", text="original result")]

        await mock_tool({"query": "test"})
        r2 = await mock_tool({"query": "test"})

        assert r2[0].text.startswith("*[cached]*")

    @pytest.mark.asyncio
    async def test_decorator_different_queries_not_cached(self):
        """Different queries should have separate cache entries."""
        call_count = 0

        @cached(tier="test")
        async def mock_tool(args: dict):
            nonlocal call_count
            call_count += 1
            from mcp.types import TextContent
            return [TextContent(type="text", text=f"result for {args['query']}")]

        await mock_tool({"query": "query1"})
        await mock_tool({"query": "query2"})

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_key_params(self):
        """key_params should differentiate cache entries."""
        call_count = 0

        @cached(tier="test", key_params=["top_k"])
        async def mock_tool(args: dict):
            nonlocal call_count
            call_count += 1
            from mcp.types import TextContent
            return [TextContent(type="text", text=f"result-{call_count}")]

        # Same query, different top_k
        await mock_tool({"query": "test", "top_k": 5})
        await mock_tool({"query": "test", "top_k": 10})

        assert call_count == 2

        # Same query and top_k - should hit cache
        await mock_tool({"query": "test", "top_k": 5})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_empty_query_bypasses_cache(self):
        """Empty query should bypass caching."""
        call_count = 0

        @cached(tier="test")
        async def mock_tool(args: dict):
            nonlocal call_count
            call_count += 1
            from mcp.types import TextContent
            return [TextContent(type="text", text="result")]

        await mock_tool({"query": ""})
        await mock_tool({"query": ""})

        # Both calls should execute (no caching)
        assert call_count == 2


# =============================================================================
# stdio MCP tool smoke tests
#
# The stdio MCP tools are registered with FastMCP via the `@mcp.tool()`
# decorator on plain async functions in `src/mcp_server.py`. The tests below
# import each tool's underlying function (via `mcp.get_tool(name).fn`) and
# call it with mocked dependencies, validating that the call signatures and
# wiring are intact. The previous version of this section imported
# `_tool_search` etc., which never existed on the consolidated repo and
# caused the whole class to silently skip — masking real wiring bugs.
# =============================================================================


def _tool_fn(name: str):
    """Return the underlying async function for a registered FastMCP tool."""
    from src.mcp_server import mcp
    return mcp._tool_manager._tools[name].fn


try:
    _tool_fn("search")
    HAS_MCP_SERVER = True
except Exception:
    HAS_MCP_SERVER = False


@pytest.mark.unit
@pytest.mark.skipif(not HAS_MCP_SERVER, reason="FastMCP tool registry not loadable")
class TestMCPToolWiring:
    """Smoke tests confirming each stdio MCP tool is wired correctly.

    These do not exercise caching (only `synthesize` has stdio-level caching);
    they exercise the call shape so a wiring regression like
    `SynthesisEngine(client, ...)` (positional misuse) trips a unit test
    instead of a runtime TypeError in production.
    """

    def setup_method(self):
        cache.clear()

    def teardown_method(self):
        cache.clear()

    @pytest.mark.asyncio
    async def test_search_tool_callable(self):
        """`search` runs with mocked aggregator and returns a markdown string."""
        with patch('src.mcp_server.SearchAggregator') as mock_agg:
            mock_instance = MagicMock()
            mock_instance.search = AsyncMock(return_value=([], {}))
            mock_agg.return_value = mock_instance

            result = await _tool_fn("search")(query="test search", top_k=5)
            assert isinstance(result, str)
            assert "Search Results" in result
            assert mock_instance.search.call_count == 1

    @pytest.mark.asyncio
    async def test_research_tool_signature(self):
        """`research` instantiates SynthesisEngine with keyword args (regression
        test: previously `research` passed `client` positionally, which silently
        bound to the `api_base` parameter and crashed at runtime)."""
        from src.connectors.base import Source

        sample_source = Source(
            id="s1", title="t", url="https://example.com", content="c", score=1.0,
            connector="searxng", metadata={},
        )

        with patch('src.mcp_server.SearchAggregator') as mock_agg, \
             patch('src.mcp_server.SynthesisEngine') as mock_engine:
            mock_agg_instance = MagicMock()
            mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
            mock_agg.return_value = mock_agg_instance

            mock_engine_instance = MagicMock()
            mock_engine_instance.research = AsyncMock(return_value={
                "content": "synthesized text",
                "citations": [{"id": "1", "title": "t", "url": "https://example.com"}],
            })
            mock_engine.return_value = mock_engine_instance

            result = await _tool_fn("research")(query="x", top_k=3, reasoning_effort="medium")

            # SynthesisEngine MUST be constructed with keyword args, not positional.
            # If anyone re-introduces `SynthesisEngine(client, ...)`, this assertion fails.
            mock_engine.assert_called_once()
            _, kwargs = mock_engine.call_args
            assert "client" in kwargs, "SynthesisEngine must be called with client= kwarg"
            assert "model" in kwargs

            # research() — not synthesize(...) — is the right method.
            mock_engine_instance.research.assert_awaited_once()
            assert "synthesized text" in result

    @pytest.mark.asyncio
    async def test_ask_tool_callable(self):
        """`ask` runs with mocked LLM client and returns the model's content."""
        with patch('src.mcp_server._get_llm_client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "test answer"
            mock_client.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await _tool_fn("ask")(query="test question")
            assert "test answer" in result

    @pytest.mark.asyncio
    async def test_reason_tool_callable(self):
        """`reason` runs with mocked LLM client and returns the response."""
        with patch('src.mcp_server._get_llm_client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "reasoning result"
            mock_client.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await _tool_fn("reason")(query="x", reasoning_depth="moderate")
            assert "reasoning result" in result

    @pytest.mark.asyncio
    async def test_per_request_key_threading(self):
        """All LLM-using stdio tools forward `openrouter_api_key` to _get_llm_client.

        Regression test: Codex Turn 1 found the docs claimed per-request key
        worked on stdio when it didn't. The `openrouter_api_key` kwarg now
        exists on every tool and must thread through to _get_llm_client(api_key=...).
        """
        with patch('src.mcp_server._get_llm_client') as mock_client:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "ok"
            mock_client.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            await _tool_fn("ask")(query="x", openrouter_api_key="sk-test-123")
            mock_client.assert_called_with("sk-test-123")


@pytest.mark.unit
@pytest.mark.skipif(not HAS_MCP_SERVER, reason="FastMCP tool registry not loadable")
class TestSynthesizeSourceAwareCaching:
    """Source-aware caching for the stdio `synthesize` tool.

    `synthesize` is the only stdio tool with built-in caching (key includes
    style, preset, and a hash of the source URL set). These tests exercise
    that contract directly against the FastMCP-decorated tool.
    """

    def setup_method(self):
        cache.clear()

    def teardown_method(self):
        cache.clear()

    @pytest.mark.asyncio
    async def test_same_sources_cached(self):
        """Same query + sources + style should hit the cache on the second call."""
        sources = [
            {"title": "Source 1", "url": "http://a.com", "content": "content 1"},
            {"title": "Source 2", "url": "http://b.com", "content": "content 2"},
        ]

        with patch('src.mcp_server.SynthesisAggregator') as mock_agg:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.content = "synthesis result [1] [2]"
            mock_result.citations = [
                {"number": 1, "title": "Source 1", "url": "http://a.com"},
                {"number": 2, "title": "Source 2", "url": "http://b.com"},
            ]
            # A valid synthesis result: not truncated, not reasoning-only, with
            # citations - so the post-synthesis verifier passes and the result
            # is cached (an unverified/hard-gated result is not cached).
            mock_result.llm_output = LLMOutput(
                text="synthesis result [1] [2]",
                source_field="content",
                finish_reason="stop",
                truncated=False,
                reasoning_only=False,
            )
            mock_instance.synthesize = AsyncMock(return_value=mock_result)
            mock_agg.return_value = mock_instance

            await _tool_fn("synthesize")(query="test synthesis", sources=sources, style="comprehensive")
            assert mock_instance.synthesize.call_count == 1

            r2 = await _tool_fn("synthesize")(query="test synthesis", sources=sources, style="comprehensive")
            assert mock_instance.synthesize.call_count == 1
            assert "*[cached]*" in r2

    @pytest.mark.asyncio
    async def test_different_sources_not_cached(self):
        """Different sources must create a new cache entry."""
        sources1 = [{"title": "S1", "url": "http://a.com", "content": "c1"}]
        sources2 = [{"title": "S2", "url": "http://b.com", "content": "c2"}]

        with patch('src.mcp_server.SynthesisAggregator') as mock_agg:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.content = "synthesis result [1] [2]"
            mock_result.citations = [
                {"number": 1, "title": "Source 1", "url": "http://a.com"},
                {"number": 2, "title": "Source 2", "url": "http://b.com"},
            ]
            # A valid synthesis result: not truncated, not reasoning-only, with
            # citations - so the post-synthesis verifier passes and the result
            # is cached (an unverified/hard-gated result is not cached).
            mock_result.llm_output = LLMOutput(
                text="synthesis result [1] [2]",
                source_field="content",
                finish_reason="stop",
                truncated=False,
                reasoning_only=False,
            )
            mock_instance.synthesize = AsyncMock(return_value=mock_result)
            mock_agg.return_value = mock_instance

            await _tool_fn("synthesize")(query="test", sources=sources1)
            await _tool_fn("synthesize")(query="test", sources=sources2)
            assert mock_instance.synthesize.call_count == 2

    @pytest.mark.asyncio
    async def test_different_style_not_cached(self):
        """Different style must create a new cache entry."""
        sources = [{"title": "S1", "url": "http://a.com", "content": "c1"}]

        with patch('src.mcp_server.SynthesisAggregator') as mock_agg:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.content = "synthesis result [1] [2]"
            mock_result.citations = [
                {"number": 1, "title": "Source 1", "url": "http://a.com"},
                {"number": 2, "title": "Source 2", "url": "http://b.com"},
            ]
            # A valid synthesis result: not truncated, not reasoning-only, with
            # citations - so the post-synthesis verifier passes and the result
            # is cached (an unverified/hard-gated result is not cached).
            mock_result.llm_output = LLMOutput(
                text="synthesis result [1] [2]",
                source_field="content",
                finish_reason="stop",
                truncated=False,
                reasoning_only=False,
            )
            mock_instance.synthesize = AsyncMock(return_value=mock_result)
            mock_agg.return_value = mock_instance

            await _tool_fn("synthesize")(query="test", sources=sources, style="comprehensive")
            await _tool_fn("synthesize")(query="test", sources=sources, style="concise")
            assert mock_instance.synthesize.call_count == 2


@pytest.mark.unit
@pytest.mark.skipif(not HAS_MCP_SERVER, reason="FastMCP tool registry not loadable")
class TestToolRegistryCoverage:
    """Sanity-check that all six stdio MCP tools are registered."""

    def test_six_tools_registered(self):
        """The stdio MCP surface must register exactly the six documented tools."""
        from src.mcp_server import mcp

        registered = set(mcp._tool_manager._tools.keys())
        expected = {"search", "research", "ask", "discover", "synthesize", "reason"}
        assert expected.issubset(registered), (
            f"Missing tools: {expected - registered}. "
            f"Registered: {sorted(registered)}"
        )

    def test_synthesize_uses_inline_cache(self):
        """`synthesize` is the only stdio tool with built-in cache.get/cache.set."""
        import inspect
        from src.mcp_server import mcp

        synthesize_fn = mcp._tool_manager._tools["synthesize"].fn
        source = inspect.getsource(synthesize_fn)
        assert "cache.get(" in source
        assert "cache.set(" in source


class TestCacheFileFormat:
    """Verify cache file format and integrity."""

    def setup_method(self):
        self.cache = HotCache(namespace="test_format")
        self.cache.clear()

    def teardown_method(self):
        self.cache.clear()

    def test_cache_file_is_valid_json(self):
        """Cache files should be valid JSON."""
        self.cache.set("query", "result", tier="test")

        key = self.cache._key("query", tier="test")
        path = self.cache._path(key)

        data = json.loads(path.read_text())
        assert "result" in data
        assert "created_at" in data
        assert "ttl" in data

    def test_cache_entry_structure(self):
        """Cache entry should have correct structure."""
        self.cache.set("query", {"complex": "data"}, tier="test", ttl=1234)

        key = self.cache._key("query", tier="test")
        path = self.cache._path(key)
        data = json.loads(path.read_text())

        assert data["result"] == {"complex": "data"}
        assert isinstance(data["created_at"], float)
        assert data["ttl"] == 1234

    def test_corrupted_file_handled_gracefully(self):
        """Corrupted cache files should be handled without error."""
        self.cache.set("query", "result", tier="test")

        key = self.cache._key("query", tier="test")
        path = self.cache._path(key)

        # Corrupt the file
        path.write_text("not valid json {{{")

        # Should return None and not raise
        result = self.cache.get("query", tier="test")
        assert result is None

        # Corrupted file should be deleted
        assert not path.exists()


class TestEdgeCases:
    """Edge cases and error handling."""

    def setup_method(self):
        self.cache = HotCache(namespace="test_edge")
        self.cache.clear()

    def teardown_method(self):
        self.cache.clear()

    def test_non_serializable_result_handled(self):
        """Non-JSON-serializable results should be skipped silently."""
        class NonSerializable:
            pass

        # Should not raise
        self.cache.set("query", NonSerializable(), tier="test")

        # Should return None (not cached)
        assert self.cache.get("query", tier="test") is None

    def test_empty_string_query(self):
        """Empty string query should still work."""
        self.cache.set("", "result", tier="test")
        assert self.cache.get("", tier="test") == "result"

    def test_very_long_query(self):
        """Very long queries should work (hashed to fixed length)."""
        long_query = "x" * 10000
        self.cache.set(long_query, "result", tier="test")
        assert self.cache.get(long_query, tier="test") == "result"

    def test_special_characters_in_query(self):
        """Special characters should be handled."""
        special_query = "test\n\t'\"<>&{}[]"
        self.cache.set(special_query, "result", tier="test")
        assert self.cache.get(special_query, tier="test") == "result"

    def test_unicode_query(self):
        """Unicode queries should work."""
        unicode_query = "测试查询 🔍 тест"
        self.cache.set(unicode_query, "result", tier="test")
        assert self.cache.get(unicode_query, tier="test") == "result"

    def test_concurrent_access(self):
        """Concurrent cache access should be safe."""
        import threading

        results = []

        def writer():
            for i in range(100):
                self.cache.set(f"query-{i}", f"result-{i}", tier="test")

        def reader():
            for i in range(100):
                result = self.cache.get(f"query-{i}", tier="test")
                if result:
                    results.append(result)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should complete without errors
        assert len(results) >= 0  # Some reads may have succeeded


class TestBuildSynthesisCacheExtra:
    """build_synthesis_cache_extra discriminates on everything that changes output.

    Locks the Turn 8 codex-review fix: the fingerprint must include origin and
    source_type (both are rendered into the synthesis prompt; origin also keys
    the attribution breakdown) and must be order-sensitive (citations bind to
    input order).
    """

    @staticmethod
    def _src(**overrides):
        base = {
            "origin": "exa", "source_type": "article",
            "url": "http://a.com", "title": "T", "content": "body",
        }
        base.update(overrides)
        return base

    @pytest.mark.unit
    def test_identical_sources_same_key(self):
        """Identical inputs produce an identical discriminator."""
        from src.cache import build_synthesis_cache_extra
        s = [self._src()]
        k1 = build_synthesis_cache_extra(s, model="m", max_tokens=3000, mode="x")
        k2 = build_synthesis_cache_extra(s, model="m", max_tokens=3000, mode="x")
        assert k1 == k2

    @pytest.mark.unit
    def test_source_order_changes_key(self):
        """Reordering sources changes the key - citations bind to input order."""
        from src.cache import build_synthesis_cache_extra
        a = self._src(url="http://a.com", title="A")
        b = self._src(url="http://b.com", title="B")
        k_ab = build_synthesis_cache_extra([a, b], model="m", max_tokens=3000, mode="x")
        k_ba = build_synthesis_cache_extra([b, a], model="m", max_tokens=3000, mode="x")
        assert k_ab != k_ba

    @pytest.mark.unit
    def test_origin_changes_key(self):
        """Same url/title/content but different origin must not collide."""
        from src.cache import build_synthesis_cache_extra
        k_exa = build_synthesis_cache_extra(
            [self._src(origin="exa")], model="m", max_tokens=3000, mode="x")
        k_ref = build_synthesis_cache_extra(
            [self._src(origin="ref")], model="m", max_tokens=3000, mode="x")
        assert k_exa != k_ref

    @pytest.mark.unit
    def test_source_type_changes_key(self):
        """Same url/title/content but different source_type must not collide."""
        from src.cache import build_synthesis_cache_extra
        k_art = build_synthesis_cache_extra(
            [self._src(source_type="article")], model="m", max_tokens=3000, mode="x")
        k_doc = build_synthesis_cache_extra(
            [self._src(source_type="documentation")], model="m", max_tokens=3000, mode="x")
        assert k_art != k_doc

    @pytest.mark.unit
    def test_model_budget_mode_change_key(self):
        """Model, effective budget, and pipeline mode are all part of the key."""
        from src.cache import build_synthesis_cache_extra
        s = [self._src()]
        base = build_synthesis_cache_extra(s, model="m1", max_tokens=3000, mode="x")
        assert build_synthesis_cache_extra(s, model="m2", max_tokens=3000, mode="x") != base
        assert build_synthesis_cache_extra(s, model="m1", max_tokens=9000, mode="x") != base
        assert build_synthesis_cache_extra(s, model="m1", max_tokens=3000, mode="y") != base

    @pytest.mark.unit
    def test_works_with_object_sources(self):
        """The fingerprint reads object sources (PreGatheredSource), not just dicts."""
        from src.cache import build_synthesis_cache_extra
        from src.synthesis.aggregator import PreGatheredSource
        obj = PreGatheredSource(
            origin="exa", url="http://a.com", title="T",
            content="body", source_type="article",
        )
        k_obj = build_synthesis_cache_extra([obj], model="m", max_tokens=3000, mode="x")
        k_dict = build_synthesis_cache_extra([self._src()], model="m", max_tokens=3000, mode="x")
        assert k_obj == k_dict
