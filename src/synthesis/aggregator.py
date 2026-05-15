"""
Pure Synthesis Aggregator

Drives the SYNTHESIS workflow: weave pre-gathered content into a coherent,
citation-aware narrative without re-searching the web. The agent supplies
sources from Ref / Exa / Jina (or any other reader); this aggregator focuses
purely on synthesis quality.

Key difference from search+synthesis:
- Does NOT perform searches
- Takes PRE-GATHERED content from Ref/Exa/Jina
- Weaves into coherent narrative with attribution

This is the final step in the synthesis workflow:
Triple Stack (Ref + Exa + Jina) → This Aggregator → Final Output
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import LLMOutput, ExtractionMode, call_with_extraction, derive_effective_budget
from .source_formatting import derive_input_budget, format_sources_for_synthesis


class SynthesisStyle(str, Enum):
    """Style of synthesis output."""
    COMPREHENSIVE = "comprehensive"  # Full analysis with sections
    CONCISE = "concise"             # Brief, focused answer
    COMPARATIVE = "comparative"      # Side-by-side analysis
    TUTORIAL = "tutorial"           # Step-by-step guide format
    ACADEMIC = "academic"           # Scholarly tone with citations


@dataclass
class PreGatheredSource:
    """A source that was pre-fetched by Ref/Exa/Jina."""
    origin: str           # "ref", "exa", "jina", or custom
    url: str
    title: str
    content: str          # Full content (already fetched)
    source_type: str      # "documentation", "code", "article", etc.
    metadata: dict = field(default_factory=dict)


@dataclass
class AggregatedSynthesis:
    """Result of synthesis aggregation."""
    content: str
    citations: list[dict]
    source_attribution: dict[str, float]  # origin -> contribution %
    confidence: float
    style_used: SynthesisStyle
    word_count: int
    llm_output: Optional[LLMOutput] = None  # provenance/truncation signal from the synthesis call


# Synthesis prompts optimized for aggregation (not search)
COMPREHENSIVE_SYNTHESIS_PROMPT = """You are synthesizing research findings from multiple pre-gathered sources.

Query: {query}

Sources have been gathered from:
- Documentation (Ref): Official docs, API references
- Code Context (Exa): Code examples, implementations
- Web Content (Jina): Articles, discussions, tutorials

Pre-gathered content:
{sources}

Instructions:
1. Synthesize these sources into a comprehensive response
2. Use inline citations [1], [2], etc. corresponding to source numbers
3. Every factual claim MUST have a citation
4. Highlight where sources agree and where they differ
5. Note any gaps in the available information
6. Structure with clear sections if content warrants it

Provide a thorough synthesis:"""

CONCISE_SYNTHESIS_PROMPT = """Synthesize these pre-gathered sources into a focused, concise answer.

Query: {query}

Sources:
{sources}

Instructions:
1. Provide a direct, concise answer (2-4 paragraphs max)
2. Use inline citations [1], [2], etc.
3. Focus on the most important points
4. Skip tangential information

Concise synthesis:"""

COMPARATIVE_SYNTHESIS_PROMPT = """Create a comparative analysis from these pre-gathered sources.

Query: {query}

Sources:
{sources}

Instructions:
1. Identify the items/approaches being compared
2. Create a structured comparison (can use tables if helpful)
3. Cite sources for each comparison point [1], [2], etc.
4. Conclude with situational recommendations

Comparative analysis:"""

ACADEMIC_SYNTHESIS_PROMPT = """Synthesize these sources in an academic/scholarly style.

Query: {query}

Sources:
{sources}

Instructions:
1. Use formal, scholarly tone
2. Cite sources rigorously [1], [2], etc.
3. Acknowledge limitations and uncertainties
4. Structure as: Background → Analysis → Discussion → Conclusions

Academic synthesis:"""

REASONING_SYNTHESIS_PROMPT = """You are synthesizing research findings with explicit reasoning.

Query: {query}

Sources:
{sources}

First, think through your approach:
1. What are the key aspects of this query?
2. Which sources are most relevant to each aspect?
3. Are there any contradictions between sources?
4. What can be confidently stated vs what is uncertain?

<reasoning>
[Your step-by-step reasoning here]
</reasoning>

Now provide your synthesis based on this reasoning:

<synthesis>
[Your synthesized response with citations [1], [2], etc.]
</synthesis>"""


class SynthesisAggregator:
    """
    Pure synthesis aggregator for pre-gathered content.

    Optimized for the SYNTHESIS workflow:
    - Input: content already fetched by Ref / Exa / Jina (or any other reader)
    - Output: coherent synthesis with attribution
    - NO additional searching
    """

    STYLE_PROMPTS = {
        SynthesisStyle.COMPREHENSIVE: COMPREHENSIVE_SYNTHESIS_PROMPT,
        SynthesisStyle.CONCISE: CONCISE_SYNTHESIS_PROMPT,
        SynthesisStyle.COMPARATIVE: COMPARATIVE_SYNTHESIS_PROMPT,
        SynthesisStyle.ACADEMIC: ACADEMIC_SYNTHESIS_PROMPT,
        SynthesisStyle.TUTORIAL: COMPREHENSIVE_SYNTHESIS_PROMPT,  # Use comprehensive as base
    }

    def __init__(
        self,
        llm_client,
        model: str = None,
    ):
        """
        Initialize the aggregator.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for synthesis
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model

    async def synthesize(
        self,
        query: str,
        sources: list[PreGatheredSource],
        style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
        max_tokens: int = 3000,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> AggregatedSynthesis:
        """
        Synthesize pre-gathered sources into coherent output.

        Args:
            query: The original research query
            sources: Pre-gathered sources from Ref/Exa/Jina
            style: Synthesis style to use
            max_tokens: Answer-budget base for the synthesis call
            guidance: Optional per-source advisory summaries (aligned with
                `sources`), rendered as a section distinct from the evidence.
            contradiction_notes: Optional cross-source contradiction analysis,
                rendered as its own advisory section - never merged into a
                source's content.

        Returns:
            AggregatedSynthesis with content and metadata
        """
        if not sources:
            return AggregatedSynthesis(
                content="No sources provided for synthesis.",
                citations=[],
                source_attribution={},
                confidence=0.0,
                style_used=style,
                word_count=0,
            )

        # Select prompt based on style
        prompt_template = self.STYLE_PROMPTS.get(
            style, COMPREHENSIVE_SYNTHESIS_PROMPT
        )

        # Budget source content against the model's context window: render the
        # prompt with empty sources to measure the fixed overhead per call.
        fixed_overhead = prompt_template.format(query=query, sources="")
        effective_output_budget = derive_effective_budget(max_tokens, self.model)
        input_budget = derive_input_budget(
            self.model, effective_output_budget, fixed_overhead
        )

        # Format sources for prompt (budget-aware; no source dropped)
        formatted_sources = self._format_sources(
            sources,
            input_budget,
            guidance=guidance,
            contradiction_notes=contradiction_notes,
        )

        prompt = prompt_template.format(
            query=query,
            sources=formatted_sources,
        )

        # Generate synthesis at the model-aware effective budget (a reasoning
        # model needs headroom to reason AND answer; the bare answer-budget
        # base would be consumed by chain-of-thought).
        output = await self._call_llm(prompt, effective_output_budget, mode=ExtractionMode.FINAL_ANSWER)
        content = output.text

        # FINAL_ANSWER fail-fast: an empty result (truly empty, or a
        # reasoning-only trace that FINAL_ANSWER mode refuses) is not a
        # synthesis. Return it honestly degraded rather than running
        # citation/confidence logic over "".
        if not content:
            return AggregatedSynthesis(
                content="",
                citations=[],
                source_attribution={},
                confidence=0.0,
                style_used=style,
                word_count=0,
                llm_output=output,
            )

        # Extract citations and compute attribution
        citations = self._extract_citations(content, sources)
        attribution = self._compute_attribution(citations, sources)
        confidence = self._estimate_confidence(sources, citations)

        return AggregatedSynthesis(
            content=content,
            citations=citations,
            source_attribution=attribution,
            confidence=confidence,
            style_used=style,
            word_count=len(content.split()),
            llm_output=output,
        )

    async def synthesize_with_reasoning(
        self,
        query: str,
        sources: list[PreGatheredSource],
        max_tokens: int = 4000,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> AggregatedSynthesis:
        """
        Synthesize with explicit reasoning.

        Uses chain-of-thought to show reasoning process before the final
        answer. Unlike `synthesize`, this method does not accept a style —
        the chain-of-thought prompt is fixed because the reasoning shape is
        what matters here, not the prose register. If you need style
        variants, call `synthesize` directly. `max_tokens` is the
        answer-budget base; the model-aware effective budget is derived from it.
        """
        # Budget source content against the model's context window.
        fixed_overhead = REASONING_SYNTHESIS_PROMPT.format(query=query, sources="")
        effective_output_budget = derive_effective_budget(max_tokens, self.model)
        input_budget = derive_input_budget(
            self.model, effective_output_budget, fixed_overhead
        )
        formatted_sources = self._format_sources(
            sources,
            input_budget,
            guidance=guidance,
            contradiction_notes=contradiction_notes,
        )

        prompt = REASONING_SYNTHESIS_PROMPT.format(
            query=query,
            sources=formatted_sources,
        )

        output = await self._call_llm(prompt, max_tokens=effective_output_budget, mode=ExtractionMode.LENIENT)
        response = output.text

        # Extract just the synthesis portion. A missing <synthesis> tag means
        # the model did not produce the requested structure - that is a
        # failure, not raw chain-of-thought to dump as if it were the answer.
        synthesis_match = re.search(
            r'<synthesis>(.*?)</synthesis>',
            response,
            re.DOTALL
        )
        if not synthesis_match:
            return AggregatedSynthesis(
                content="",
                citations=[],
                source_attribution={},
                confidence=0.0,
                style_used=SynthesisStyle.COMPREHENSIVE,
                word_count=0,
                llm_output=output,
            )
        content = synthesis_match.group(1).strip()

        # The result is the extracted <synthesis> block - a real answer,
        # regardless of which response field carried it. The call was made in
        # LENIENT mode (the chain-of-thought IS expected here), so `output`
        # may carry reasoning_only=True; that flag describes the raw call, not
        # this extracted result. Carry a post-extraction signal so a verifier
        # does not false-positive on reasoning_only. `truncated` is preserved -
        # a truncated call can still mean an incomplete synthesis block.
        result_output = LLMOutput(
            text=content,
            source_field="content",
            finish_reason=output.finish_reason,
            truncated=output.truncated,
            reasoning_only=False,
        )

        citations = self._extract_citations(content, sources)
        attribution = self._compute_attribution(citations, sources)

        return AggregatedSynthesis(
            content=content,
            citations=citations,
            source_attribution=attribution,
            confidence=self._estimate_confidence(sources, citations),
            # `reason` does not select a style; the field is preserved on the
            # response shape for parity with `synthesize`, set to the chain-of-
            # thought default.
            style_used=SynthesisStyle.COMPREHENSIVE,
            word_count=len(content.split()),
            llm_output=result_output,
        )

    def _format_sources(
        self,
        sources: list[PreGatheredSource],
        input_budget_tokens: int,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> str:
        """Format sources for the prompt, budget-aware (see source_formatting)."""
        return format_sources_for_synthesis(
            sources,
            input_budget_tokens,
            guidance=guidance,
            contradiction_notes=contradiction_notes,
        )

    def _extract_citations(
        self,
        text: str,
        sources: list[PreGatheredSource],
    ) -> list[dict]:
        """Extract citations from synthesized text."""
        citations = []
        seen = set()

        # Find all [N] patterns
        pattern = re.compile(r'\[(\d+)\]')
        for match in pattern.finditer(text):
            try:
                idx = int(match.group(1)) - 1  # Convert to 0-indexed
                if 0 <= idx < len(sources) and idx not in seen:
                    source = sources[idx]
                    citations.append({
                        "number": idx + 1,
                        "title": source.title,
                        "url": source.url,
                        "origin": source.origin,
                        "source_type": source.source_type,
                    })
                    seen.add(idx)
            except (ValueError, IndexError):
                continue

        return citations

    def _compute_attribution(
        self,
        citations: list[dict],
        sources: list[PreGatheredSource],
    ) -> dict[str, float]:
        """Compute attribution breakdown by source origin."""
        if not citations:
            return {}

        origin_counts = {}
        for citation in citations:
            origin = citation.get("origin", "unknown")
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

        total = sum(origin_counts.values())
        return {
            origin: count / total
            for origin, count in origin_counts.items()
        }

    def _estimate_confidence(
        self,
        sources: list[PreGatheredSource],
        citations: list[dict],
    ) -> float:
        """Estimate confidence based on source quality and citation coverage."""
        if not sources:
            return 0.0

        # Base confidence from number of sources
        source_confidence = min(len(sources) / 5, 1.0) * 0.3

        # Citation coverage
        citation_ratio = len(citations) / max(len(sources), 1)
        citation_confidence = min(citation_ratio, 1.0) * 0.3

        # Source diversity (different origins)
        origins = set(s.origin for s in sources)
        diversity_confidence = min(len(origins) / 3, 1.0) * 0.2

        # Source type quality
        quality_types = {"documentation", "academic", "official"}
        quality_sources = [
            s for s in sources
            if s.source_type.lower() in quality_types
        ]
        quality_confidence = min(len(quality_sources) / max(len(sources), 1), 1.0) * 0.2

        return source_confidence + citation_confidence + diversity_confidence + quality_confidence

    async def _call_llm(
        self,
        prompt: str,
        max_tokens: int = 3000,
        *,
        mode: ExtractionMode,
    ) -> LLMOutput:
        """Call LLM with prompt and extract output according to `mode`."""
        return await call_with_extraction(
            self.llm_client,
            self.model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            mode,
            temperature=0.7,
        )
