"""FastMCP server for deep-research tools.

Exposes research tools via Model Context Protocol using stdio transport.
Uses LLMClient for inference against any OpenAI-compatible chat-completions
endpoint (vLLM, SGLang, llama.cpp, OpenRouter).

Usage:
    python -m src.mcp_server
"""

import os
from typing import Literal

# Suppress FastMCP logging before import to avoid polluting stdio transport
os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

from fastmcp import FastMCP
import fastmcp
fastmcp.settings.log_level = "ERROR"

from .config import settings
from .llm_client import get_llm_client
from .llm_utils import get_llm_content, derive_effective_budget
from .search import SearchAggregator
from .synthesis import (
    SynthesisEngine,
    SynthesisAggregator,
    SynthesisStyle,
    PreGatheredSource,
    SourceQualityGate,
    QualityDecision,
    ContradictionDetector,
    OutlineGuidedSynthesizer,
    RCSPreprocessor,
    get_preset,
    verify_synthesis_output,
    annotate_with_verdict,
)
from .discovery import (
    Explorer,
    FocusModeType,
    FocusModeSelector,
    get_focus_mode,
    get_search_params,
)
from .cache import cache, cached, build_synthesis_cache_extra


# Initialize FastMCP server
mcp = FastMCP("deepresearch")


def _get_llm_client(api_key: str | None = None):
    """Get LLM client with optional per-request key override."""
    return get_llm_client(api_key=api_key)


@mcp.tool()
async def search(
    query: str,
    top_k: int = 10,
    api_key: str | None = None,
) -> str:
    """Multi-source search with RRF (Reciprocal Rank Fusion).

    Returns ranked results from SearXNG, Tavily, and LinkUp.
    Use for raw search results without synthesis. No LLM call.

    Args:
        query: Search query
        top_k: Results per source (1-50)
        api_key: Per-request LLM key override; ignored by `search` since
            no LLM call is made, but accepted for consistency across tools.
    """
    # search makes no LLM call, but we accept api_key for surface consistency
    # so callers can use the same shape across all six tools.
    _ = api_key
    aggregator = SearchAggregator()
    sources, raw_results = await aggregator.search(query=query, top_k=top_k)

    lines = [f"# Search Results for: {query}\n"]
    for i, s in enumerate(sources, 1):
        lines.append(f"## [{i}] {s.title}")
        lines.append(f"**URL:** {s.url}")
        lines.append(f"**Source:** {s.connector} (score: {s.score:.3f})")
        lines.append(f"\n{s.content[:500]}{'...' if len(s.content) > 500 else ''}\n")

    lines.append(f"\n---\n*{len(sources)} results from {list(raw_results.keys())}*")
    return "\n".join(lines)


@mcp.tool()
async def research(
    query: str,
    top_k: int = 10,
    reasoning_effort: Literal["low", "medium", "high"] = "medium",
    api_key: str | None = None,
) -> str:
    """Full research pipeline: search + LLM synthesis with citations.

    Pipeline: Multi-source search → Source aggregation → LLM synthesis → Citation formatting

    Args:
        query: Research query
        top_k: Results per source
        reasoning_effort: Depth of analysis (low=concise, medium=balanced, high=academic)
        api_key: Per-request LLM key override; defaults to RESEARCH_LLM_API_KEY.
    """
    aggregator = SearchAggregator()
    sources, _ = await aggregator.search(query=query, top_k=top_k)

    if not sources:
        return "No sources found for query."

    client = _get_llm_client(api_key)
    engine = SynthesisEngine(client=client, model=settings.llm_model)

    result = await engine.research(
        query=query,
        sources=sources,
        reasoning_effort=reasoning_effort,
    )

    lines = [f"# Research: {query}\n"]
    lines.append(result.get("content", ""))
    citations = result.get("citations", [])
    if citations:
        lines.append("\n## Citations\n")
        for c in citations:
            lines.append(f"- [{c['id']}] [{c['title']}]({c['url']})")

    return "\n".join(lines)


@mcp.tool()
async def ask(
    query: str,
    context: str = "",
    api_key: str | None = None,
) -> str:
    """Quick conversational answer using LLM.

    No search, direct response from model knowledge.
    Use for simple factual questions or follow-ups.

    Args:
        query: Question to answer
        context: Optional context to consider
        api_key: Per-request LLM key override; defaults to RESEARCH_LLM_API_KEY.
    """
    client = _get_llm_client(api_key)

    messages = []
    if context:
        messages.append({"role": "system", "content": f"Context: {context}"})
    messages.append({"role": "user", "content": query})

    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )

    return get_llm_content(response.choices[0].message)


@mcp.tool()
async def discover(
    query: str,
    top_k: int = 10,
    identify_gaps: bool = True,
    focus_mode: Literal["general", "academic", "documentation", "comparison", "debugging", "tutorial", "news"] = "general",
    api_key: str | None = None,
) -> str:
    """Exploratory discovery with knowledge gap analysis.

    Identifies what's known and unknown about a topic.
    Use for cold-start exploration.

    Args:
        query: Topic to explore
        top_k: Results per source
        identify_gaps: Analyze knowledge gaps
        focus_mode: Domain-specific discovery mode
        api_key: Per-request LLM key override; defaults to RESEARCH_LLM_API_KEY.
    """
    client = _get_llm_client(api_key)
    aggregator = SearchAggregator()

    try:
        focus_mode_type = FocusModeType(focus_mode.lower())
    except ValueError:
        focus_mode_type = FocusModeType.GENERAL

    focus_config = get_focus_mode(focus_mode)
    search_params = get_search_params(focus_mode_type)
    expand_searches = search_params.get("expand_searches", True)

    explorer = Explorer(client, aggregator, model=settings.llm_model)
    result = await explorer.discover(
        query=query,
        top_k=top_k,
        expand_searches=expand_searches,
        fill_gaps=identify_gaps,
    )

    lines = [f"# Discovery: {query}\n"]
    lines.append(f"*Focus Mode: {focus_config.name}* - {focus_config.description}\n")

    lines.append("## Knowledge Landscape\n")
    if hasattr(result, 'landscape') and result.landscape:
        landscape = result.landscape
        lines.append(f"**Explicit Topics:** {', '.join(landscape.explicit_topics)}")
        if landscape.implicit_topics:
            lines.append(f"**Implicit Topics:** {', '.join(landscape.implicit_topics)}")
        if landscape.related_concepts:
            lines.append(f"**Related Concepts:** {', '.join(landscape.related_concepts)}")
    else:
        lines.append("Exploration complete.")

    if identify_gaps and hasattr(result, 'knowledge_gaps') and result.knowledge_gaps:
        lines.append("\n## Knowledge Gaps\n")
        gap_categories = focus_config.gap_categories
        for gap in result.knowledge_gaps:
            gap_type = getattr(gap, 'category', None) or gap.gap.lower()
            relevance = "🎯 " if any(cat in gap_type for cat in gap_categories) else ""
            lines.append(f"- {relevance}**{gap.gap}** ({gap.importance}): {gap.description}")

    lines.append(f"\n## Sources ({len(result.sources)})\n")
    for s in result.sources[:5]:
        lines.append(f"- [{s.source.title}]({s.source.url})")

    if hasattr(result, 'recommended_deep_dives') and result.recommended_deep_dives:
        lines.append("\n## Recommended Deep Dives\n")
        for url in result.recommended_deep_dives[:5]:
            lines.append(f"- {url}")

    lines.append(f"\n---\n*Search expansion: {'enabled' if expand_searches else 'disabled'}*")
    if focus_config.gap_categories:
        lines.append(f"*Gap focus: {', '.join(focus_config.gap_categories)}*")

    return "\n".join(lines)


@mcp.tool()
async def synthesize(
    query: str,
    sources: list[dict],
    style: Literal["comprehensive", "concise", "comparative", "academic", "tutorial"] = "comprehensive",
    preset: Literal["comprehensive", "fast", "contracrow", "academic", "tutorial"] | None = None,
    api_key: str | None = None,
) -> str:
    """Synthesize pre-gathered content into coherent analysis.

    Use when you already have sources from other tools.

    Args:
        query: Synthesis focus/question
        sources: Pre-gathered source documents with title, content, url, origin, source_type
        style: Output format/length
        preset: Processing pipeline preset (comprehensive, fast, contracrow, academic, tutorial)
        api_key: Per-request LLM key override; defaults to RESEARCH_LLM_API_KEY.
    """
    # Source-aware cache key: fingerprint source content in input order plus
    # model + effective budget + pipeline mode + version, so a reorder or a
    # content/model/budget change never returns a stale or mis-bound result.
    base_max_tokens = get_preset(preset).max_tokens if preset else settings.llm_max_tokens
    effective_max_tokens = derive_effective_budget(base_max_tokens, settings.llm_model)
    cache_extra = build_synthesis_cache_extra(
        sources,
        model=settings.llm_model,
        max_tokens=effective_max_tokens,
        mode=f"preset={preset}:style={style}",
    )

    cached_result = cache.get(query, tier="synthesis", extra=cache_extra)
    if cached_result is not None:
        return f"*[cached]*\n\n{cached_result}"

    # Convert to PreGatheredSource
    pre_sources = [
        PreGatheredSource(
            origin=s.get("origin", "external"),
            url=s.get("url", ""),
            title=s["title"],
            content=s["content"],
            source_type=s.get("source_type", "article"),
        )
        for s in sources
    ]

    client = _get_llm_client(api_key)

    style_map = {
        "comprehensive": SynthesisStyle.COMPREHENSIVE,
        "concise": SynthesisStyle.CONCISE,
        "comparative": SynthesisStyle.COMPARATIVE,
        "academic": SynthesisStyle.ACADEMIC,
        "tutorial": SynthesisStyle.TUTORIAL,
    }
    synth_style = style_map.get(style, SynthesisStyle.COMPREHENSIVE)

    if preset:
        preset_config = get_preset(preset)
        metadata = {"preset": preset_config.name}
        processed_sources = pre_sources

        # Quality gate
        if preset_config.run_quality_gate:
            quality_gate = SourceQualityGate(client, model=settings.llm_model)
            gate_result = await quality_gate.evaluate(query=query, sources=pre_sources)
            if gate_result.decision in (QualityDecision.PARTIAL, QualityDecision.PROCEED):
                processed_sources = gate_result.good_sources if gate_result.good_sources else pre_sources
            metadata["quality_gate"] = {
                "passed": len(gate_result.good_sources),
                "filtered": len(gate_result.rejected_sources),
                "avg_quality": gate_result.avg_quality,
            }

        # RCS preprocessing (guidance-only: the contextual summaries become
        # advisory guidance passed alongside the full sources - they never
        # replace source content or drop sources)
        rcs_guidance = None
        if preset_config.use_rcs and processed_sources:
            rcs = RCSPreprocessor(client, model=settings.llm_model)
            rcs_result = await rcs.prepare(query=query, sources=processed_sources)
            if rcs_result.summaries:
                rcs_guidance = [cs.summary for cs in rcs_result.summaries]
            metadata["rcs_applied"] = True
            metadata["rcs_kept"] = len(rcs_result.summaries)

        # Contradiction detection
        contradictions = []
        detection = None
        if preset_config.detect_contradictions and processed_sources:
            detector = ContradictionDetector(client, model=settings.llm_model)
            detection = await detector.detect(query=query, sources=processed_sources)
            contradictions = detection.contradictions
            metadata["contradictions_found"] = len(contradictions)

        # Synthesis (full sources reach the model; RCS summaries ride along as
        # advisory guidance). preset.max_tokens is the answer-budget base; the
        # synthesis methods derive the model-aware effective budget from it.
        if preset_config.use_outline:
            outline_synth = OutlineGuidedSynthesizer(client, model=settings.llm_model)
            result = await outline_synth.synthesize(query=query, sources=processed_sources, style=synth_style, max_tokens=preset_config.max_tokens, guidance=rcs_guidance)
        else:
            aggregator = SynthesisAggregator(client, model=settings.llm_model)
            result = await aggregator.synthesize(query=query, sources=processed_sources, style=synth_style, max_tokens=preset_config.max_tokens, guidance=rcs_guidance)

        lines = [f"# Synthesis: {query}\n"]
        lines.append(f"*Preset: {preset_config.name}*\n")
        lines.append(result.content)

        if contradictions:
            lines.append("\n## Contradictions Detected\n")
            for c in contradictions:
                lines.append(f"- **{c.topic}** ({c.severity.value}): {c.position_a} vs {c.position_b}")
                if c.resolution_hint:
                    lines.append(f"  - Resolution: {c.resolution_hint}")

        if hasattr(result, 'citations') and result.citations:
            lines.append("\n## Citations\n")
            for c in result.citations:
                if hasattr(c, 'title'):
                    lines.append(f"- [{c.id}] [{c.title}]({c.url})")
                else:
                    lines.append(f"- [{c.get('number', '?')}] [{c.get('title', 'Unknown')}]({c.get('url', '')})")

        if metadata.get("quality_gate"):
            qg = metadata["quality_gate"]
            lines.append(
                f"\n---\n*Pre-synthesis source-relevance gate: {qg['passed']} passed, "
                f"{qg['filtered']} filtered (avg source relevance: {qg['avg_quality']:.2f}). "
                f"Scores input source relevance, not output quality.*"
            )
        if metadata.get("rcs_applied"):
            lines.append(f"*RCS: {metadata.get('rcs_kept', 0)} sources processed*")

        # Post-synthesis verification: a failed synthesis must not be
        # reported as a success or cached.
        verdict = verify_synthesis_output(
            content=result.content,
            llm_output=result.llm_output,
            cited_count=len(result.citations) if getattr(result, "citations", None) else 0,
            source_count=len(processed_sources),
            contradiction_result=detection,
        )
        output = annotate_with_verdict("\n".join(lines), verdict)
        if verdict.passed:
            cache.set(query, output, tier="synthesis", extra=cache_extra)
        return output

    # Standard synthesis (no preset). settings.llm_max_tokens is the
    # answer-budget base; the aggregator derives the effective budget from it.
    aggregator = SynthesisAggregator(client, model=settings.llm_model)
    result = await aggregator.synthesize(query=query, sources=pre_sources, style=synth_style, max_tokens=settings.llm_max_tokens)

    lines = [f"# Synthesis: {query}\n"]
    lines.append(result.content)

    if result.citations:
        lines.append("\n## Citations\n")
        for c in result.citations:
            lines.append(f"- [{c.get('number', '?')}] [{c.get('title', 'Unknown')}]({c.get('url', '')})")

    # Post-synthesis verification.
    verdict = verify_synthesis_output(
        content=result.content,
        llm_output=result.llm_output,
        cited_count=len(result.citations) if result.citations else 0,
        source_count=len(pre_sources),
        contradiction_result=None,
    )
    output = annotate_with_verdict("\n".join(lines), verdict)
    if verdict.passed:
        cache.set(query, output, tier="synthesis", extra=cache_extra)
    return output


@mcp.tool()
async def reason(
    query: str,
    context: str = "",
    sources: list[dict] | None = None,
    reasoning_depth: Literal["shallow", "moderate", "deep"] = "moderate",
    api_key: str | None = None,
) -> str:
    """Deep reasoning with chain-of-thought analysis.

    Two modes, picked automatically by whether `sources` is provided:

    - **No-sources mode** (default): direct chain-of-thought over the model's
      own knowledge plus optional `context`. Use for problems the model can
      reason about without external evidence; depth-controlled via
      `reasoning_depth`.
    - **Sources-aware mode** (when `sources` is non-empty): chain-of-thought
      synthesis over the pre-gathered sources, with the same shape REST
      `/api/v1/reason` produces. `reasoning_depth` is ignored in this mode —
      the chain-of-thought prompt is fixed because the reasoning shape is
      what matters here, not the prose register.

    For style variants over pre-gathered sources, call `synthesize` directly.

    Args:
        query: Problem or question requiring reasoning
        context: Background information or constraints (no-sources mode only)
        sources: Pre-gathered sources to reason over. If provided, switches to
            sources-aware mode and uses chain-of-thought synthesis.
        reasoning_depth: How thorough (no-sources mode only).
            shallow=2-3 steps, moderate=4-6, deep=7+
        api_key: Per-request LLM key override; defaults to RESEARCH_LLM_API_KEY.
    """
    client = _get_llm_client(api_key)

    if sources:
        pre_sources = [
            PreGatheredSource(
                origin=s.get("origin", "external"),
                url=s.get("url", ""),
                title=s["title"],
                content=s["content"],
                source_type=s.get("source_type", "article"),
            )
            for s in sources
        ]

        aggregator = SynthesisAggregator(client, model=settings.llm_model)
        result = await aggregator.synthesize_with_reasoning(
            query=query,
            sources=pre_sources,
        )

        lines = [f"# Reasoning: {query}\n"]
        lines.append(result.content)
        if result.citations:
            lines.append("\n## Citations\n")
            for c in result.citations:
                lines.append(f"- [{c.get('number', '?')}] [{c.get('title', 'Unknown')}]({c.get('url', '')})")
        # Post-synthesis verification: the sources-aware reason path is a
        # synthesize surface, so a degraded/empty result must not be
        # relayed as a clean answer. Apply the same verifier used by the
        # MCP `synthesize` and REST `/synthesize*` paths.
        verdict = verify_synthesis_output(
            content=result.content,
            llm_output=result.llm_output,
            cited_count=len(result.citations) if result.citations else 0,
            source_count=len(pre_sources),
            contradiction_result=None,
        )
        return annotate_with_verdict("\n".join(lines), verdict)

    depth_prompts = {
        "shallow": "Provide a brief analysis.",
        "moderate": "Think through this step-by-step, showing your reasoning.",
        "deep": "Analyze this comprehensively. Consider multiple perspectives, potential counterarguments, edge cases, and implications. Show detailed chain-of-thought reasoning.",
    }

    system_prompt = f"""You are a reasoning assistant. {depth_prompts.get(reasoning_depth, depth_prompts['moderate'])}

Structure your response with clear sections for:
1. Understanding the problem
2. Key considerations
3. Step-by-step reasoning
4. Conclusion"""

    messages = [{"role": "system", "content": system_prompt}]
    if context:
        messages.append({"role": "user", "content": f"Context: {context}"})
    messages.append({"role": "user", "content": query})

    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.7,
        max_tokens=settings.llm_max_tokens,
    )

    return get_llm_content(response.choices[0].message)


if __name__ == "__main__":
    settings.require_llm_key()
    mcp.run(show_banner=False)
