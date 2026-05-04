"""
Outline-Guided Synthesis.

Research basis: SciRAG (arXiv:2511.14362)
- Step 1: Generate outline from query + sources
- Step 2: Fill each section with cited content
- Step 3: Critique the draft for gaps/errors
- Step 4: Refine based on critique

Key insight: Planning before synthesis improves structure and coverage.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..llm_utils import get_llm_content
from .aggregator import PreGatheredSource, SynthesisStyle


@dataclass
class SynthesisOutline:
    """Structure for synthesis planning."""
    sections: list[str]
    rationale: str = ""


@dataclass
class CritiqueResult:
    """Issues found during critique."""
    issues: list[str]
    has_critical: bool = False


@dataclass
class OutlinedSynthesis:
    """Result of outline-guided synthesis."""
    content: str
    outline: SynthesisOutline
    sections: dict[str, str]
    critique: Optional[CritiqueResult] = None
    refined: bool = False
    word_count: int = 0


class OutlineGuidedSynthesizer:
    """
    Synthesize using plan-critique-refine cycle.

    Usage:
        synthesizer = OutlineGuidedSynthesizer(llm_client)
        result = await synthesizer.synthesize(
            "Compare FastAPI vs Flask",
            sources,
            style=SynthesisStyle.COMPARATIVE
        )
        # Returns structured outline-guided synthesis
    """

    OUTLINE_PROMPT = """Create an outline for synthesizing these sources to answer the query.

Query: {query}

Available sources cover:
{source_summary}

Create 3-6 section headings that would best structure a {style} response.
Format: One heading per line, no numbers or bullets."""

    SECTION_PROMPT = """Write the "{section}" section for this research synthesis.

Query: {query}

Sources:
{sources}

Instructions:
- Write 1-3 paragraphs for this section
- Use [N] citations for factual claims
- Focus on information relevant to "{section}"
- Be specific and informative

Section content:"""

    CRITIQUE_PROMPT = """Critique this draft synthesis for quality issues.

Query: {query}

Draft:
{draft}

Available source content (for reference):
{source_summary}

Identify any issues:
1. Missing important information from sources
2. Uncited factual claims
3. Unclear or confusing explanations
4. Logical gaps or inconsistencies
5. Sections that need more depth

Format: One issue per line, or respond with "NO_ISSUES" if the draft is good."""

    REFINE_PROMPT = """Refine this synthesis to address the identified issues.

Original draft:
{draft}

Issues to address:
{issues}

Sources for reference:
{sources}

Provide the improved synthesis with proper [N] citations:"""

    def __init__(
        self,
        llm_client,
        model: str = None,
        max_refinement_rounds: int = 1,
    ):
        self.llm_client = llm_client
        self.model = model
        self.max_refinement_rounds = max_refinement_rounds

    async def synthesize(
        self,
        query: str,
        sources: list[PreGatheredSource],
        style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
        max_tokens_per_section: int = 800,
    ) -> OutlinedSynthesis:
        """
        Synthesize using plan-critique-refine cycle.

        Args:
            query: Research query
            sources: Pre-gathered sources
            style: Synthesis style
            max_tokens_per_section: Max tokens per section

        Returns:
            OutlinedSynthesis with structure and content
        """
        if not sources:
            return OutlinedSynthesis(
                content="No sources provided for synthesis.",
                outline=SynthesisOutline(sections=[]),
                sections={},
                word_count=0,
            )

        # Step 1: Generate outline
        outline = await self._generate_outline(query, sources, style)

        # Step 2: Fill each section
        sections = {}
        for section in outline.sections:
            sections[section] = await self._fill_section(
                section, query, sources, max_tokens_per_section
            )

        # Step 3: Assemble draft
        draft = self._assemble(outline.sections, sections)

        # Step 4: Critique
        critique = await self._critique(draft, query, sources)

        # Step 5: Refine if needed
        refined = False
        if critique.issues and self.max_refinement_rounds > 0:
            draft = await self._refine(draft, critique.issues, sources)
            refined = True

        return OutlinedSynthesis(
            content=draft,
            outline=outline,
            sections=sections,
            critique=critique if critique.issues else None,
            refined=refined,
            word_count=len(draft.split()),
        )

    async def _generate_outline(
        self,
        query: str,
        sources: list[PreGatheredSource],
        style: SynthesisStyle,
    ) -> SynthesisOutline:
        """Generate outline from query and sources."""
        source_summary = self._summarize_sources(sources)

        prompt = self.OUTLINE_PROMPT.format(
            query=query,
            source_summary=source_summary,
            style=style.value,
        )

        response = await self._call_llm(prompt, max_tokens=300)
        sections = [
            s.strip() for s in response.strip().split("\n")
            if s.strip() and not s.strip().startswith("#")
        ]

        # Ensure reasonable number of sections
        if len(sections) < 2:
            sections = ["Overview", "Details", "Conclusion"]
        elif len(sections) > 8:
            sections = sections[:8]

        return SynthesisOutline(sections=sections)

    async def _fill_section(
        self,
        section: str,
        query: str,
        sources: list[PreGatheredSource],
        max_tokens: int,
    ) -> str:
        """Fill a single section with content."""
        formatted_sources = self._format_sources(sources)

        prompt = self.SECTION_PROMPT.format(
            section=section,
            query=query,
            sources=formatted_sources,
        )

        return await self._call_llm(prompt, max_tokens=max_tokens)

    async def _critique(
        self,
        draft: str,
        query: str,
        sources: list[PreGatheredSource],
    ) -> CritiqueResult:
        """Critique the draft for issues."""
        source_summary = self._summarize_sources(sources)

        prompt = self.CRITIQUE_PROMPT.format(
            query=query,
            draft=draft,
            source_summary=source_summary,
        )

        response = await self._call_llm(prompt, max_tokens=500)

        if "NO_ISSUES" in response.upper():
            return CritiqueResult(issues=[], has_critical=False)

        issues = [
            line.strip() for line in response.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]

        # Check for critical issues
        critical_keywords = ["missing", "uncited", "incorrect", "wrong"]
        has_critical = any(
            any(kw in issue.lower() for kw in critical_keywords)
            for issue in issues
        )

        return CritiqueResult(issues=issues, has_critical=has_critical)

    async def _refine(
        self,
        draft: str,
        issues: list[str],
        sources: list[PreGatheredSource],
    ) -> str:
        """Refine the draft based on critique."""
        formatted_sources = self._format_sources(sources)
        issues_text = "\n".join(f"- {issue}" for issue in issues)

        prompt = self.REFINE_PROMPT.format(
            draft=draft,
            issues=issues_text,
            sources=formatted_sources,
        )

        return await self._call_llm(prompt, max_tokens=3000)

    def _assemble(self, section_titles: list[str], sections: dict[str, str]) -> str:
        """Assemble sections into final document."""
        parts = []
        for title in section_titles:
            content = sections.get(title, "")
            if content:
                parts.append(f"## {title}\n\n{content}")
        return "\n\n".join(parts)

    def _summarize_sources(self, sources: list[PreGatheredSource]) -> str:
        """Create brief summary of available sources."""
        summaries = []
        for i, s in enumerate(sources, 1):
            summaries.append(f"[{i}] {s.title} ({s.origin}): {s.content[:200]}...")
        return "\n".join(summaries)

    def _format_sources(
        self,
        sources: list[PreGatheredSource],
        max_per_source: int = 1500,
    ) -> str:
        """Format sources for prompts."""
        parts = []
        for i, s in enumerate(sources, 1):
            content = s.content[:max_per_source]
            if len(s.content) > max_per_source:
                content += "..."
            parts.append(f"[{i}] {s.title}\n{content}")
        return "\n\n".join(parts)

    async def _call_llm(self, prompt: str, max_tokens: int = 1000) -> str:
        """Call LLM with prompt."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return get_llm_content(response.choices[0].message)


# Heuristic fallback for outline generation
def generate_outline_heuristic(
    query: str,
    style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
) -> SynthesisOutline:
    """Generate basic outline without LLM."""
    query_lower = query.lower()

    # Detect query patterns
    if "vs" in query_lower or "compare" in query_lower or "difference" in query_lower:
        sections = [
            "Overview",
            "Key Differences",
            "Pros and Cons",
            "Use Case Recommendations",
        ]
    elif "how to" in query_lower or "tutorial" in query_lower:
        sections = [
            "Prerequisites",
            "Step-by-Step Guide",
            "Common Issues",
            "Next Steps",
        ]
    elif "what is" in query_lower or "explain" in query_lower:
        sections = [
            "Definition",
            "Key Concepts",
            "Examples",
            "Related Topics",
        ]
    elif style == SynthesisStyle.ACADEMIC:
        sections = [
            "Background",
            "Analysis",
            "Discussion",
            "Conclusions",
        ]
    else:
        sections = [
            "Overview",
            "Key Points",
            "Details",
            "Summary",
        ]

    return SynthesisOutline(sections=sections)
