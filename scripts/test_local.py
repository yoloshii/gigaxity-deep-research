#!/usr/bin/env python3
"""Quick test script for local development."""

import asyncio
import sys
sys.path.insert(0, ".")

from src.search import SearchAggregator
from src.synthesis import SynthesisEngine
from src.connectors import SearXNGConnector


async def test_searxng():
    """Test SearXNG connector."""
    print("Testing SearXNG...")
    connector = SearXNGConnector()

    if not connector.is_configured():
        print("  SearXNG not configured")
        return

    result = await connector.search("python fastapi tutorial", top_k=3)
    print(f"  Found {len(result.sources)} results")
    for s in result.sources[:2]:
        print(f"    - {s.title[:50]}... ({s.url[:40]}...)")


async def test_aggregator():
    """Test search aggregator with RRF fusion."""
    print("\nTesting aggregator with RRF fusion...")
    aggregator = SearchAggregator()

    print(f"  Active connectors: {aggregator.get_active_connectors()}")

    if not aggregator.connectors:
        print("  No connectors configured")
        return

    sources, raw = await aggregator.search("machine learning frameworks 2024", top_k=5)
    print(f"  Fused {len(sources)} sources from {list(raw.keys())}")
    for s in sources[:3]:
        print(f"    [{s.id}] {s.title[:40]}... (score: {s.score:.4f})")


async def test_synthesis():
    """Test synthesis engine (requires LLM)."""
    print("\nTesting synthesis engine...")
    engine = SynthesisEngine()
    aggregator = SearchAggregator()

    if not aggregator.connectors:
        print("  No connectors - skipping")
        return

    sources, _ = await aggregator.search("what is retrieval augmented generation", top_k=5)

    if not sources:
        print("  No sources found")
        return

    print(f"  Synthesizing from {len(sources)} sources...")
    result = await engine.research(
        query="What is RAG (Retrieval Augmented Generation)?",
        sources=sources,
        reasoning_effort="low",
    )

    if "error" in result:
        print(f"  Error: {result['error']}")
    else:
        print(f"  Generated {len(result['content'])} chars")
        print(f"  Citations: {len(result['citations'])}")
        print(f"  Preview: {result['content'][:200]}...")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Research Tool - Local Test")
    print("=" * 60)

    await test_searxng()
    await test_aggregator()
    await test_synthesis()

    print("\n" + "=" * 60)
    print("Tests complete")


if __name__ == "__main__":
    asyncio.run(main())
