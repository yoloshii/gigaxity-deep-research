"""Expansion hardening + the exactly-once original search (rev 7, steps 6/6a).

Two defects:

1. expansion.py ran its heuristic fallback only in the except block, so a
   blank response (reasoning-starved or truncated under PARSE_REQUIRED)
   yielded "no variants" while the fallback sat unreachable; duplicates were
   filtered only after parsing, so N structurally-valid duplicates could
   yield zero effective variants and still count as success. The prompt also
   hardcoded 4 variants while Explorer asks for 3.

2. ExpandedQuery.variants includes the original first (public contract) and
   Explorer copied the whole list while _gather_sources seeds the original
   itself — so N=3 executed original·original·v1·v2: the original searched
   twice, generated variant 3 discarded.
"""

from types import SimpleNamespace

import pytest

from src.connectors.base import Source
from src.degradation import DegradationCode
from src.discovery.expansion import (
    ExpandedQuery,
    QueryExpander,
    parse_expansion_records,
)
from src.discovery.explorer import Explorer


def _blocks(*variants: str) -> str:
    return "\n".join(
        f"VARIANT: {v}\nANGLE: angle for {v}\n---" for v in variants
    )


# ---------------------------------------------------------------------------
# Grammar units
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExpansionGrammar:
    def test_exactly_n_distinct_variants(self):
        text = _blocks("qubit storage", "quantum RAM", "memory challenges")
        variants, angles = parse_expansion_records(text, 3, "quantum memory")
        assert variants == ["qubit storage", "quantum RAM", "memory challenges"]
        assert len(angles) == 3

    def test_duplicates_reduce_not_succeed(self):
        """Structurally-valid duplicates must never turn zero effective
        variants into success: dups are dropped, the result goes short."""
        text = _blocks("qubit storage", "Qubit Storage", "quantum RAM")
        variants, _ = parse_expansion_records(text, 3, "quantum memory")
        assert variants == ["qubit storage", "quantum RAM"]

    def test_variant_equal_to_original_dropped(self):
        text = _blocks("Quantum Memory", "qubit storage", "quantum RAM")
        variants, _ = parse_expansion_records(text, 3, "quantum memory")
        assert variants == ["qubit storage", "quantum RAM"]

    def test_all_duplicates_of_original_rejected(self):
        text = _blocks("quantum memory", "QUANTUM MEMORY", "Quantum Memory")
        assert parse_expansion_records(text, 3, "quantum memory") is None

    def test_more_than_n_blocks_rejected(self):
        text = _blocks("a", "b", "c", "d")
        assert parse_expansion_records(text, 3, "q") is None

    def test_incomplete_block_rejected(self):
        text = "VARIANT: qubit storage\n---"
        assert parse_expansion_records(text, 3, "q") is None

    def test_prose_rejected(self):
        text = "Here are some variants you could try searching for."
        assert parse_expansion_records(text, 3, "q") is None

    def test_blank_rejected(self):
        assert parse_expansion_records("", 3, "q") is None


# ---------------------------------------------------------------------------
# expand() outcomes
# ---------------------------------------------------------------------------


def _choice(content=None, reasoning_content=None, finish_reason="stop"):
    return SimpleNamespace(
        message=SimpleNamespace(
            content=content, reasoning_content=reasoning_content
        ),
        finish_reason=finish_reason,
    )


class OneShotClient:
    def __init__(self, choice=None, error: Exception = None):
        self._choice = choice
        self._error = error
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        if self._error:
            raise self._error
        return SimpleNamespace(choices=[self._choice])


@pytest.mark.unit
class TestExpandOutcomes:
    async def test_complete_success_original_first(self):
        client = OneShotClient(_choice(content=_blocks("v one", "v two", "v three")))
        expanded = await QueryExpander(client, model="m").expand("orig query", 3)
        assert expanded.variants == ["orig query", "v one", "v two", "v three"]
        assert expanded.angles[0] == "original query"
        assert expanded.degradations == []

    async def test_n_is_passed_into_the_prompt(self):
        """The prompt hardcoded 4 while Explorer asks for 3."""
        client = OneShotClient(_choice(content=_blocks("a", "b", "c")))
        await QueryExpander(client, model="m").expand("orig", 3)
        assert "exactly 3" in client.prompts[0]

    async def test_partial_success_recorded_not_failed(self):
        client = OneShotClient(_choice(content=_blocks("v one", "v two")))
        expanded = await QueryExpander(client, model="m").expand("orig", 3)
        assert expanded.variants == ["orig", "v one", "v two"]
        deg = expanded.degradations[0]
        assert deg.code is DegradationCode.PARTIAL
        assert deg.parse_failed is False
        assert deg.fallback_used is False

    async def test_blank_response_takes_heuristic_fallback(self):
        """The key fix: a reasoning-starved blank runs the heuristic
        expander — it no longer yields 'no variants'."""
        client = OneShotClient(
            _choice(reasoning_content="thinking...", finish_reason="length")
        )
        expanded = await QueryExpander(client, model="m").expand("orig", 3)
        assert len(expanded.variants) == 4  # original + 3 heuristic variants
        assert expanded.variants[0] == "orig"
        deg = expanded.degradations[0]
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is True
        assert deg.fallback_used is True

    async def test_malformed_response_takes_heuristic_fallback(self):
        client = OneShotClient(_choice(content="try searching other stuff"))
        expanded = await QueryExpander(client, model="m").expand("orig", 3)
        assert len(expanded.variants) == 4
        assert expanded.degradations[0].code is DegradationCode.MALFORMED

    async def test_transport_error_takes_heuristic_fallback(self):
        client = OneShotClient(error=RuntimeError("connection reset by peer"))
        expanded = await QueryExpander(client, model="m").expand("orig", 3)
        assert len(expanded.variants) == 4
        deg = expanded.degradations[0]
        assert deg.code is DegradationCode.TRANSPORT_ERROR
        assert "connection reset" not in deg.message  # sanitized

    async def test_no_client_heuristic_without_degradation(self):
        """Heuristic-only by configuration is normal operation."""
        expanded = await QueryExpander(None).expand("orig", 3)
        assert len(expanded.variants) == 4
        assert expanded.degradations == []


# ---------------------------------------------------------------------------
# 6a: the aggregator receives the original exactly once and every generated
# variant exactly once — complete, partial, and heuristic-fallback paths.
# ---------------------------------------------------------------------------


LANDSCAPE = (
    "EXPLICIT: topic\nIMPLICIT: NONE\nRELATED: NONE\nCONTRASTING: NONE"
)


class DiscoveryClient:
    """Expansion choice injectable; other stages return benign defaults."""

    def __init__(self, expansion_choice):
        self.expansion_choice = expansion_choice
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "search query variants" in prompt:
            choice = self.expansion_choice
        elif "knowledge landscape" in prompt:
            choice = _choice(content=LANDSCAPE)
        elif "identify knowledge gaps" in prompt:
            choice = _choice(content="NO_GAPS")
        elif "Score these sources" in prompt:
            n = prompt.count("\nSOURCE ")
            choice = _choice(content="\n".join(
                f"SOURCE_INDEX: {i}\nGAPS_ADDRESSED: NONE\n"
                f"UNIQUE_VALUE: v{i}\nPRIORITY: 2\n---"
                for i in range(1, n + 1)
            ))
        else:
            choice = _choice(content="Preview.")
        return SimpleNamespace(choices=[choice])


class QueryLogAggregator:
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query: str, top_k: int = 10, **kwargs):
        self.queries.append(query)
        return ([Source(id="sx", title="T", url="https://example.com/x",
                        content="c", score=0.5)], {})


async def _run_discover(expansion_choice, expander_client=True):
    aggregator = QueryLogAggregator()
    client = DiscoveryClient(expansion_choice)
    explorer = Explorer(
        llm_client=client,
        search_aggregator=aggregator,
        model="m",
        expander=QueryExpander(client if expander_client else None, model="m"),
    )
    result = await explorer.discover(
        "orig query", expand_searches=True, fill_gaps=False
    )
    return aggregator, result


@pytest.mark.unit
class TestOriginalSearchedExactlyOnce:
    async def test_complete_expansion(self):
        agg, result = await _run_discover(
            _choice(content=_blocks("v one", "v two", "v three"))
        )
        assert agg.queries.count("orig query") == 1
        for v in ("v one", "v two", "v three"):
            assert agg.queries.count(v) == 1
        assert result.degradations == []

    async def test_partial_expansion(self):
        agg, result = await _run_discover(_choice(content=_blocks("v one", "v two")))
        assert agg.queries.count("orig query") == 1
        assert agg.queries.count("v one") == 1
        assert agg.queries.count("v two") == 1
        # Partial expansion surfaced on the DiscoveryResult carrier.
        assert any(
            d.stage == "expansion" and d.code is DegradationCode.PARTIAL
            for d in result.degradations
        )

    async def test_heuristic_fallback_expansion(self):
        agg, result = await _run_discover(
            _choice(reasoning_content="thinking...", finish_reason="length")
        )
        assert agg.queries.count("orig query") == 1
        heuristic_variants = [q for q in agg.queries if q != "orig query"]
        assert len(heuristic_variants) == len(set(heuristic_variants)) == 3
        assert any(
            d.stage == "expansion" and d.fallback_used
            for d in result.degradations
        )

    async def test_expansion_degradations_reach_discovery_result(self):
        """Q6: expansion previously had no carrier at all — its failures
        could never reach DiscoveryResult and would cache as clean."""
        _, result = await _run_discover(_choice(content="garbage"))
        assert any(d.stage == "expansion" for d in result.degradations)
