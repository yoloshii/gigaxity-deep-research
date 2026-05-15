"""Performance benchmark tests for the research tool.

Evaluates:
- Synthesis latency and throughput
- RCS preprocessing performance
- Outline generation performance
- Quality gate evaluation time
- End-to-end pipeline performance

Run with: pytest tests/test_performance.py -v -m benchmark
"""

import time
import statistics
import pytest
from dataclasses import dataclass


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""
    operation: str
    iterations: int
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    std_dev_ms: float

    def __str__(self) -> str:
        return (
            f"{self.operation}: "
            f"mean={self.mean_ms:.2f}ms, "
            f"median={self.median_ms:.2f}ms, "
            f"min={self.min_ms:.2f}ms, "
            f"max={self.max_ms:.2f}ms, "
            f"std={self.std_dev_ms:.2f}ms "
            f"(n={self.iterations})"
        )


def benchmark(operation: str, func, iterations: int = 5) -> BenchmarkResult:
    """Run a benchmark and collect timing statistics."""
    times = []

    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return BenchmarkResult(
        operation=operation,
        iterations=iterations,
        mean_ms=statistics.mean(times),
        median_ms=statistics.median(times),
        min_ms=min(times),
        max_ms=max(times),
        std_dev_ms=statistics.stdev(times) if len(times) > 1 else 0.0,
    )


async def async_benchmark(operation: str, func, iterations: int = 5) -> BenchmarkResult:
    """Run an async benchmark and collect timing statistics."""
    times = []

    for _ in range(iterations):
        start = time.perf_counter()
        await func()
        end = time.perf_counter()
        times.append((end - start) * 1000)

    return BenchmarkResult(
        operation=operation,
        iterations=iterations,
        mean_ms=statistics.mean(times),
        median_ms=statistics.median(times),
        min_ms=min(times),
        max_ms=max(times),
        std_dev_ms=statistics.stdev(times) if len(times) > 1 else 0.0,
    )


# =============================================================================
# Heuristic Component Benchmarks (No LLM)
# =============================================================================


class TestHeuristicPerformance:
    """Benchmark heuristic-only operations (no LLM calls)."""

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_connector_router_latency(self):
        """Benchmark connector routing decision time."""
        from src.discovery import ConnectorRouter

        router = ConnectorRouter()
        queries = [
            "Python async programming tutorial",
            "Compare React vs Vue",
            "TypeError: Cannot read property of undefined",
            "FastAPI documentation API reference",
            "transformer attention mechanisms arxiv paper",
        ]

        def route_queries():
            for q in queries:
                router.route_sync(q)

        result = benchmark("ConnectorRouter.route (5 queries)", route_queries, iterations=10)
        print(f"\n{result}")

        # Performance assertion: routing should be fast
        assert result.mean_ms < 50, f"Routing too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_query_expander_heuristic_latency(self):
        """Benchmark heuristic query expansion."""
        from src.discovery import QueryExpander

        expander = QueryExpander()

        def expand():
            expander.expand_sync("Python async programming best practices")

        result = benchmark("QueryExpander.expand_sync", expand, iterations=10)
        print(f"\n{result}")

        # Heuristic expansion should be very fast
        assert result.mean_ms < 20, f"Expansion too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_focus_mode_selector_latency(self):
        """Benchmark focus mode selection."""
        from src.discovery import FocusModeSelector

        selector = FocusModeSelector()
        queries = [
            "How to implement OAuth2",
            "Compare FastAPI vs Flask",
            "TypeError in React component",
            "Research papers on transformers",
            "Latest Python 3.13 features",
        ]

        def select_modes():
            for q in queries:
                selector.select_sync(q)

        result = benchmark("FocusModeSelector.select_sync (5 queries)", select_modes, iterations=10)
        print(f"\n{result}")

        assert result.mean_ms < 30, f"Mode selection too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_outline_heuristic_latency(self):
        """Benchmark heuristic outline generation."""
        from src.synthesis import generate_outline_heuristic, SynthesisStyle

        queries = [
            ("Compare FastAPI vs Flask", SynthesisStyle.COMPARATIVE),
            ("How to implement OAuth2", SynthesisStyle.TUTORIAL),
            ("What is dependency injection", SynthesisStyle.COMPREHENSIVE),
            ("Research on transformers", SynthesisStyle.ACADEMIC),
        ]

        def generate_outlines():
            for query, style in queries:
                generate_outline_heuristic(query, style)

        result = benchmark("generate_outline_heuristic (4 queries)", generate_outlines, iterations=10)
        print(f"\n{result}")

        assert result.mean_ms < 10, f"Outline generation too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_rcs_heuristic_latency(self, pre_gathered_sources):
        """Benchmark heuristic RCS preprocessing."""
        from src.synthesis import RCSPreprocessor

        rcs = RCSPreprocessor(min_relevance=0.0)

        def prepare():
            rcs.prepare_sync(
                "Compare FastAPI vs Flask performance",
                pre_gathered_sources,
                top_k=5
            )

        result = benchmark("RCSPreprocessor.prepare_sync", prepare, iterations=10)
        print(f"\n{result}")

        # RCS heuristic should be reasonably fast
        assert result.mean_ms < 100, f"RCS too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_quality_gate_heuristic_latency(self, pre_gathered_sources):
        """Benchmark heuristic quality gate evaluation."""
        from src.synthesis import SourceQualityGate

        gate = SourceQualityGate()

        def evaluate():
            gate.evaluate_sync(
                "Compare FastAPI vs Flask",
                pre_gathered_sources
            )

        result = benchmark("SourceQualityGate.evaluate_sync", evaluate, iterations=10)
        print(f"\n{result}")

        assert result.mean_ms < 50, f"Quality gate too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_contradiction_heuristic_latency(self, contradicting_sources):
        """Benchmark heuristic contradiction detection."""
        from src.synthesis import ContradictionDetector

        detector = ContradictionDetector()

        def detect():
            detector.detect_sync(
                "Is Redux necessary for React?",
                contradicting_sources
            )

        result = benchmark("ContradictionDetector.detect_sync", detect, iterations=10)
        print(f"\n{result}")

        assert result.mean_ms < 50, f"Contradiction detection too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_citation_verifier_latency(self):
        """Benchmark citation verification."""
        from src.synthesis import CitationVerifier

        verifier = CitationVerifier()

        def verify():
            verifier.verify(
                claim="FastAPI is fast and modern",
                evidence="FastAPI is a modern, fast web framework for building APIs.",
                source_number=1
            )

        # Warm up first — initial cold-import cost can add ~200ms to the first
        # call, which skews the mean for a 10-iteration run. We assert against
        # the steady-state median to capture typical performance.
        verify()
        result = benchmark("CitationVerifier.verify", verify, iterations=10)
        print(f"\n{result}")

        assert result.median_ms < 20, f"Citation verification too slow at the median: {result.median_ms}ms"


# =============================================================================
# LLM-Based Component Benchmarks
# =============================================================================


class TestLLMPerformance:
    """Benchmark LLM-powered operations."""

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_query_expansion_llm_latency(self, llm_client):
        """Benchmark LLM query expansion."""
        from src.discovery import QueryExpander
        from src.config import settings

        expander = QueryExpander(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        async def expand():
            await expander.expand("FastAPI authentication best practices")

        result = await async_benchmark("QueryExpander.expand (LLM)", expand, iterations=3)
        print(f"\n{result}")

        # LLM calls take longer but should be reasonable
        assert result.mean_ms < 10000, f"Expansion too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_rcs_llm_latency(self, llm_client, pre_gathered_sources):
        """Benchmark LLM RCS preprocessing."""
        from src.synthesis import RCSPreprocessor
        from src.config import settings

        rcs = RCSPreprocessor(
            llm_client=llm_client,
            model=settings.llm_model,
            min_relevance=0.2,
        )

        async def prepare():
            await rcs.prepare(
                "FastAPI performance and features",
                pre_gathered_sources,
            )

        result = await async_benchmark("RCSPreprocessor.prepare (LLM)", prepare, iterations=3)
        print(f"\n{result}")

        # RCS with LLM processes each source
        assert result.mean_ms < 30000, f"RCS too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_outline_synthesis_llm_latency(self, llm_client, pre_gathered_sources):
        """Benchmark LLM outline-guided synthesis."""
        from src.synthesis import OutlineGuidedSynthesizer, SynthesisStyle
        from src.config import settings

        synthesizer = OutlineGuidedSynthesizer(
            llm_client=llm_client,
            model=settings.llm_model,
            max_refinement_rounds=0,  # Skip refinement for speed
        )

        async def synthesize():
            await synthesizer.synthesize(
                "Compare FastAPI vs Flask",
                pre_gathered_sources,
                style=SynthesisStyle.COMPARATIVE,
                max_tokens=2000,
            )

        result = await async_benchmark("OutlineGuidedSynthesizer.synthesize (LLM)", synthesize, iterations=2)
        print(f"\n{result}")

        # Outline synthesis does multiple LLM calls
        assert result.mean_ms < 60000, f"Outline synthesis too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_quality_gate_llm_latency(self, llm_client, pre_gathered_sources):
        """Benchmark LLM quality gate evaluation."""
        from src.synthesis import SourceQualityGate
        from src.config import settings

        gate = SourceQualityGate(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        async def evaluate():
            await gate.evaluate(
                "Compare FastAPI vs Flask",
                pre_gathered_sources
            )

        result = await async_benchmark("SourceQualityGate.evaluate (LLM)", evaluate, iterations=3)
        print(f"\n{result}")

        assert result.mean_ms < 15000, f"Quality gate too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_contradiction_llm_latency(self, llm_client, contradicting_sources):
        """Benchmark LLM contradiction detection."""
        from src.synthesis import ContradictionDetector
        from src.config import settings

        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        async def detect():
            await detector.detect(
                "Is Redux necessary for React?",
                contradicting_sources
            )

        result = await async_benchmark("ContradictionDetector.detect (LLM)", detect, iterations=3)
        print(f"\n{result}")

        assert result.mean_ms < 15000, f"Contradiction detection too slow: {result.mean_ms}ms"


# =============================================================================
# End-to-End Pipeline Benchmarks
# =============================================================================


class TestPipelinePerformance:
    """Benchmark full pipeline operations."""

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_full_synthesis_pipeline(self, llm_client, pre_gathered_sources):
        """Benchmark complete synthesis pipeline."""
        from src.synthesis import (
            SourceQualityGate,
            ContradictionDetector,
            RCSPreprocessor,
            SynthesisAggregator,
            SynthesisStyle,
        )
        from src.config import settings

        async def run_pipeline():
            # Step 1: Quality Gate
            gate = SourceQualityGate(llm_client=llm_client, model=settings.llm_model)
            gate_result = await gate.evaluate("Compare FastAPI vs Flask", pre_gathered_sources)

            # Step 2: RCS
            rcs = RCSPreprocessor(llm_client=llm_client, model=settings.llm_model)
            rcs_result = await rcs.prepare(
                "Compare FastAPI vs Flask",
                gate_result.good_sources,
            )

            # Step 3: Contradiction detection
            detector = ContradictionDetector(llm_client=llm_client, model=settings.llm_model)
            contradictions = await detector.detect(
                "Compare FastAPI vs Flask",
                [s.source for s in rcs_result.summaries]
            )

            # Step 4: Synthesis
            aggregator = SynthesisAggregator(llm_client=llm_client, model=settings.llm_model)
            result = await aggregator.synthesize(
                "Compare FastAPI vs Flask",
                [s.source for s in rcs_result.summaries],
                style=SynthesisStyle.COMPARATIVE,
                max_tokens=2000,
            )

            return result

        result = await async_benchmark("Full Synthesis Pipeline", run_pipeline, iterations=2)
        print(f"\n{result}")

        # Full pipeline should complete within reasonable time
        assert result.mean_ms < 120000, f"Full pipeline too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    def test_fast_preset_pipeline(self, test_client, llm_configured):
        """Benchmark fast preset end-to-end."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        def run_fast_synthesis():
            response = test_client.post("/api/v1/synthesize/p1", json={
                "query": "What is FastAPI?",
                "sources": [
                    {
                        "origin": "ref",
                        "url": "https://fastapi.tiangolo.com/",
                        "title": "FastAPI Documentation",
                        "content": "FastAPI is a modern, fast web framework for building APIs with Python.",
                        "source_type": "documentation"
                    }
                ],
                "preset": "fast"
            })
            assert response.status_code == 200

        result = benchmark("Fast Preset E2E", run_fast_synthesis, iterations=3)
        print(f"\n{result}")

        # Fast preset should be... fast
        assert result.mean_ms < 15000, f"Fast preset too slow: {result.mean_ms}ms"

    @pytest.mark.benchmark
    @pytest.mark.integration
    @pytest.mark.slow
    def test_tutorial_preset_pipeline(self, test_client, llm_configured):
        """Benchmark tutorial preset end-to-end (outline-guided, OpenRouter-optimized)."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        def run_tutorial_synthesis():
            response = test_client.post("/api/v1/synthesize/p1", json={
                "query": "How to build REST APIs with FastAPI vs Flask",
                "sources": [
                    {
                        "origin": "ref",
                        "url": "https://fastapi.tiangolo.com/",
                        "title": "FastAPI Documentation",
                        "content": "FastAPI is a modern, fast web framework for building APIs with Python 3.7+ based on standard Python type hints.",
                        "source_type": "documentation"
                    },
                    {
                        "origin": "exa",
                        "url": "https://flask.palletsprojects.com/",
                        "title": "Flask Documentation",
                        "content": "Flask is a lightweight WSGI web application framework designed to make getting started quick and easy.",
                        "source_type": "documentation"
                    }
                ],
                "preset": "tutorial"
            })
            assert response.status_code == 200

        result = benchmark("Tutorial Preset E2E", run_tutorial_synthesis, iterations=2)
        print(f"\n{result}")

        # Tutorial preset with outline should complete within timeout
        assert result.mean_ms < 60000, f"Tutorial preset too slow: {result.mean_ms}ms"


# =============================================================================
# Throughput Tests
# =============================================================================


class TestThroughput:
    """Test throughput under load."""

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_heuristic_throughput(self):
        """Test heuristic operations throughput."""
        from src.discovery import ConnectorRouter, QueryExpander, FocusModeSelector
        from src.synthesis import (
            generate_outline_heuristic,
            SynthesisStyle,
            CitationVerifier,
        )

        router = ConnectorRouter()
        expander = QueryExpander()
        selector = FocusModeSelector()
        verifier = CitationVerifier()

        queries = [
            "How to implement OAuth2 in FastAPI",
            "Compare React vs Vue vs Angular",
            "TypeError: Cannot read property of undefined",
            "Research papers on transformer attention",
            "Latest Python 3.13 features",
        ] * 20  # 100 queries

        start = time.perf_counter()

        for query in queries:
            router.route_sync(query)
            expander.expand_sync(query)
            selector.select_sync(query)
            generate_outline_heuristic(query, SynthesisStyle.COMPREHENSIVE)
            verifier.verify("Test claim", "Test evidence", 1)

        end = time.perf_counter()
        total_ms = (end - start) * 1000
        ops_per_query = 5
        total_ops = len(queries) * ops_per_query
        ops_per_second = total_ops / (total_ms / 1000)

        print(f"\nHeuristic Throughput:")
        print(f"  Total queries: {len(queries)}")
        print(f"  Total operations: {total_ops}")
        print(f"  Total time: {total_ms:.2f}ms")
        print(f"  Throughput: {ops_per_second:.0f} ops/sec")

        # Should handle many operations per second
        assert ops_per_second > 100, f"Throughput too low: {ops_per_second} ops/sec"


# =============================================================================
# Memory Usage Tests (basic)
# =============================================================================


class TestMemoryUsage:
    """Basic memory usage tests."""

    @pytest.mark.benchmark
    @pytest.mark.unit
    def test_large_source_handling(self):
        """Test handling of large source content."""
        from src.synthesis import RCSPreprocessor, PreGatheredSource

        # Create sources with large content
        large_content = "Test content. " * 10000  # ~120KB per source
        sources = [
            PreGatheredSource(
                origin=f"source_{i}",
                url=f"https://example.com/{i}",
                title=f"Large Source {i}",
                content=large_content,
                source_type="article",
            )
            for i in range(20)  # 20 large sources
        ]

        rcs = RCSPreprocessor(min_relevance=0.0)

        start = time.perf_counter()
        result = rcs.prepare_sync("Test query", sources, top_k=10)
        end = time.perf_counter()

        print(f"\nLarge Source Handling:")
        print(f"  Sources: {len(sources)}")
        print(f"  Content per source: ~{len(large_content)} chars")
        print(f"  Total content: ~{len(large_content) * len(sources) / 1024:.0f}KB")
        print(f"  Processing time: {(end-start)*1000:.2f}ms")
        print(f"  Result sources: {result.kept_sources}")

        # Should handle without excessive delay
        assert (end - start) < 5, "Large source handling too slow"
