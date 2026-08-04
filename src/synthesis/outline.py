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

from ..config import settings
from ..degradation import StageDegradation, degradation_from_output
from ..llm_utils import LLMOutput, ExtractionMode, call_with_extraction, combine_llm_outputs, derive_effective_budget
from .aggregator import PreGatheredSource, SynthesisStyle
from .citations import CITATION_FORMAT_GUIDE, EVIDENCE_DISCIPLINE
from .source_formatting import derive_input_budget, format_sources_for_synthesis


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
    # Critique output was unusable (starved/truncated/malformed). issues=[]
    # with parse_failed=True means "critique unavailable", NOT "no issues" —
    # conflating them silently skips the refine pass.
    parse_failed: bool = False


@dataclass
class OutlinedSynthesis:
    """Result of outline-guided synthesis."""
    content: str
    outline: SynthesisOutline
    sections: dict[str, str]
    critique: Optional[CritiqueResult] = None
    refined: bool = False
    word_count: int = 0
    llm_output: Optional[LLMOutput] = None  # provenance/truncation signal from the synthesis calls
    degradations: list[StageDegradation] = field(default_factory=list)


# Stage grammars (lenient-parsed-callsites design, rev 7). Structural, not
# punctuation heuristics: every non-empty line must be a complete record.
# Payload bounds may be tuned against a fixture corpus; the grammars are fixed.
OUTLINE_MIN_SECTIONS = 3
OUTLINE_MAX_SECTIONS = 6
OUTLINE_MAX_HEADING_CHARS = 200

SYNTHETIC_CRITIQUE_ISSUE = (
    "Critique unavailable - do a general pass: verify factual claims carry "
    "citations, citations match the sources, and explanations are clear."
)


def parse_outline_records(text: str) -> Optional[list[str]]:
    """Parse `SECTION: <heading>` records; None = grammar rejected.

    Accepts exactly OUTLINE_MIN_SECTIONS..OUTLINE_MAX_SECTIONS records with
    no other non-empty lines; payloads non-empty, case-fold unique, and
    length-bounded. An outline is never legitimately empty, so blank input
    is a rejection, and a heading count outside the bounds is a rejection —
    a chain-of-thought trace split into lines must not pass as headings.
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    records = [ln for ln in lines if ln]
    if not records:
        return None
    headings: list[str] = []
    seen: set[str] = set()
    for record in records:
        if not record.startswith("SECTION:"):
            return None
        heading = record[len("SECTION:"):].strip()
        if not heading or len(heading) > OUTLINE_MAX_HEADING_CHARS:
            return None
        folded = heading.casefold()
        if folded in seen:
            return None
        seen.add(folded)
        headings.append(heading)
    if not (OUTLINE_MIN_SECTIONS <= len(headings) <= OUTLINE_MAX_SECTIONS):
        return None
    return headings


def parse_critique_records(text: str) -> Optional[list[str]]:
    """Parse critique output; [] = clean NO_ISSUES verdict, None = rejected.

    NO_ISSUES is matched against the WHOLE normalized response (quotes and a
    trailing period tolerated) — substring matching let "NO_ISSUES" inside
    arbitrary chatter count as a clean verdict. Otherwise every non-empty
    line must be a complete `ISSUE: <text>` record.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    sentinel = raw.strip('"\'').rstrip('.').strip().upper()
    if sentinel == "NO_ISSUES":
        return []
    issues: list[str] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if not ln.startswith("ISSUE:"):
            return None
        payload = ln[len("ISSUE:"):].strip()
        if not payload:
            return None
        issues.append(payload)
    return issues if issues else None


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
Format: one record per line, each exactly:
SECTION: <heading>

Output only SECTION: records - no numbering, no bullets, no other text."""

    SECTION_PROMPT = f"""Write the "{{section}}" section for this research synthesis.

Query: {{query}}

Sources:
{{sources}}

Instructions:
- Write 1-3 paragraphs for this section
- Every factual claim needs a citation
- Focus on information relevant to "{{section}}"
- Be specific and informative

{CITATION_FORMAT_GUIDE}

{EVIDENCE_DISCIPLINE}

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
6. Claims resting on weak sources (forum/social/community) as their only support
7. Inference or single-source assertions presented as settled fact

Format: one record per line, each exactly:
ISSUE: <one issue>

Output only ISSUE: records. If the draft is good, respond with exactly:
NO_ISSUES"""

    REFINE_PROMPT = f"""Refine this synthesis to address the identified issues.

Original draft:
{{draft}}

Issues to address:
{{issues}}

Sources for reference:
{{sources}}

{CITATION_FORMAT_GUIDE}

{EVIDENCE_DISCIPLINE}

Provide the improved synthesis:"""

    def __init__(
        self,
        llm_client,
        model: str = None,
        max_refinement_rounds: int = 1,
    ):
        self.llm_client = llm_client
        self.model = model or settings.llm_model
        self.max_refinement_rounds = max_refinement_rounds

    async def synthesize(
        self,
        query: str,
        sources: list[PreGatheredSource],
        style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
        max_tokens: int = 3000,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> OutlinedSynthesis:
        """
        Synthesize using plan-critique-refine cycle.

        Args:
            query: Research query
            sources: Pre-gathered sources
            style: Synthesis style
            max_tokens: Answer-budget base. The model-aware effective budget is
                derived from it once here; each section gets effective // 4 and
                the refinement pass gets the full effective budget.
            guidance: Optional per-source advisory summaries (aligned with
                `sources`), rendered as a section distinct from the evidence.
            contradiction_notes: Optional cross-source contradiction analysis,
                rendered as its own advisory section.

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

        # Model-aware effective budget, derived once: each section gets a
        # quarter share, the refinement pass gets the full budget.
        effective = derive_effective_budget(max_tokens, self.model)
        per_section_budget = max(1, effective // 4)

        degradations: list[StageDegradation] = []

        # Step 1: Generate outline
        outline, outline_deg = await self._generate_outline(query, sources, style)
        if outline_deg:
            degradations.append(outline_deg)

        # Step 2: Fill each section
        sections = {}
        section_outputs = []
        for section in outline.sections:
            section_output = await self._fill_section(
                section, query, sources, per_section_budget,
                guidance=guidance, contradiction_notes=contradiction_notes,
            )
            sections[section] = section_output.text
            section_outputs.append(section_output)

        # Step 3: Assemble draft
        draft = self._assemble(outline.sections, sections)

        # Step 4: Critique
        critique, critique_deg = await self._critique(draft, query, sources)
        if critique_deg:
            degradations.append(critique_deg)

        # Step 5: Refine if needed
        refined = False
        refine_output = None
        if critique.parse_failed:
            # Critique unusable: run exactly ONE general refinement with a
            # synthetic issue rather than silently skipping the pass that
            # rescues a degraded draft. An explicit max_refinement_rounds == 0
            # still wins and preserves the draft (the degradation reports it).
            if self.max_refinement_rounds > 0:
                refine_output = await self._refine(
                    draft, [SYNTHETIC_CRITIQUE_ISSUE], sources, effective,
                    guidance=guidance, contradiction_notes=contradiction_notes,
                )
                if refine_output.text:
                    draft = refine_output.text
                    refined = True
        elif critique.issues and self.max_refinement_rounds > 0:
            refine_output = await self._refine(
                draft, critique.issues, sources, effective,
                guidance=guidance, contradiction_notes=contradiction_notes,
            )
            # FINAL_ANSWER fail-fast: only replace the assembled draft if the
            # refine call actually produced an answer. An empty/reasoning-only
            # refine result must not discard a non-empty pre-refine draft.
            if refine_output.text:
                draft = refine_output.text
                refined = True

        # Carry a representative provenance/truncation signal for the verifier,
        # built only from the calls that actually contributed to `draft`.
        contributing = list(section_outputs)
        if refined:
            contributing.append(refine_output)
        llm_output = combine_llm_outputs(draft, contributing)

        return OutlinedSynthesis(
            content=draft,
            outline=outline,
            sections=sections,
            critique=critique if critique.issues else None,
            refined=refined,
            word_count=len(draft.split()),
            llm_output=llm_output,
            degradations=degradations,
        )

    async def _generate_outline(
        self,
        query: str,
        sources: list[PreGatheredSource],
        style: SynthesisStyle,
    ) -> tuple[SynthesisOutline, Optional[StageDegradation]]:
        """Generate outline from query and sources.

        PARSE_REQUIRED + the SECTION: grammar; on rejection fall back to the
        deterministic heuristic outline and report a degradation. The budget
        is reasoning-aware: a flat 300 starved glm-5.2's chain-of-thought
        (finish_reason=length, 0 content chars) and LENIENT then handed CoT
        fragments back as section headings.
        """
        source_summary = self._summarize_sources(sources)

        prompt = self.OUTLINE_PROMPT.format(
            query=query,
            source_summary=source_summary,
            style=style.value,
        )

        output = await self._call_llm(
            prompt,
            max_tokens=derive_effective_budget(300, self.model),
            mode=ExtractionMode.PARSE_REQUIRED,
        )
        sections = parse_outline_records(output.text)
        if sections is None:
            fallback = generate_outline_heuristic(query, style)
            return fallback, degradation_from_output(
                "outline",
                output,
                parse_failed=True,
                fallback_used=True,
                message="outline output unusable; heuristic outline substituted",
            )

        return SynthesisOutline(sections=sections), None

    async def _fill_section(
        self,
        section: str,
        query: str,
        sources: list[PreGatheredSource],
        max_tokens: int,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> LLMOutput:
        """Fill a single section with content.

        `max_tokens` is the effective per-section budget - already model-aware,
        derived once by synthesize(). Not re-derived here.
        """
        fixed_overhead = self.SECTION_PROMPT.format(
            section=section, query=query, sources=""
        )
        input_budget = derive_input_budget(self.model, max_tokens, fixed_overhead)
        formatted_sources = self._format_sources(
            sources, input_budget,
            guidance=guidance, contradiction_notes=contradiction_notes,
        )

        prompt = self.SECTION_PROMPT.format(
            section=section,
            query=query,
            sources=formatted_sources,
        )

        return await self._call_llm(prompt, max_tokens=max_tokens, mode=ExtractionMode.FINAL_ANSWER)

    async def _critique(
        self,
        draft: str,
        query: str,
        sources: list[PreGatheredSource],
    ) -> tuple[CritiqueResult, Optional[StageDegradation]]:
        """Critique the draft for issues.

        PARSE_REQUIRED + the ISSUE:/NO_ISSUES grammar. Reasoning-aware
        budget: design Q2 kept this flat-500 pending instrumentation;
        the harness (scripts/instrument_stage_budgets.py, 2026-08-04,
        glm-5.2) then showed 100% truncation at 500 — every call spent the
        whole budget on chain-of-thought and blanked. A failed critique is
        parse_failed=True, never issues=[] — "no issues" must stay
        distinguishable from "critique unusable", because issues=[] silently
        skips the refine pass that rescues a degraded draft.
        """
        source_summary = self._summarize_sources(sources)

        prompt = self.CRITIQUE_PROMPT.format(
            query=query,
            draft=draft,
            source_summary=source_summary,
        )

        output = await self._call_llm(
            prompt,
            max_tokens=derive_effective_budget(500, self.model),
            mode=ExtractionMode.PARSE_REQUIRED,
        )
        issues = parse_critique_records(output.text)
        if issues is None:
            return (
                CritiqueResult(issues=[], has_critical=False, parse_failed=True),
                degradation_from_output(
                    "critique",
                    output,
                    parse_failed=True,
                    fallback_used=True,
                    message=(
                        "critique output unusable; general refinement pass substituted"
                        if self.max_refinement_rounds > 0
                        else "critique output unusable; draft preserved unrefined"
                    ),
                ),
            )

        if not issues:
            return CritiqueResult(issues=[], has_critical=False), None

        # Check for critical issues
        critical_keywords = ["missing", "uncited", "incorrect", "wrong"]
        has_critical = any(
            any(kw in issue.lower() for kw in critical_keywords)
            for issue in issues
        )

        return CritiqueResult(issues=issues, has_critical=has_critical), None

    async def _refine(
        self,
        draft: str,
        issues: list[str],
        sources: list[PreGatheredSource],
        max_tokens: int,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> LLMOutput:
        """Refine the draft based on critique.

        `max_tokens` is the effective budget - already model-aware, derived
        once by synthesize(). Not re-derived here.
        """
        issues_text = "\n".join(f"- {issue}" for issue in issues)
        fixed_overhead = self.REFINE_PROMPT.format(
            draft=draft, issues=issues_text, sources=""
        )
        input_budget = derive_input_budget(self.model, max_tokens, fixed_overhead)
        formatted_sources = self._format_sources(
            sources, input_budget,
            guidance=guidance, contradiction_notes=contradiction_notes,
        )

        prompt = self.REFINE_PROMPT.format(
            draft=draft,
            issues=issues_text,
            sources=formatted_sources,
        )

        return await self._call_llm(prompt, max_tokens=max_tokens, mode=ExtractionMode.FINAL_ANSWER)

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
        input_budget_tokens: int,
        guidance: Optional[list[str]] = None,
        contradiction_notes: Optional[str] = None,
    ) -> str:
        """Format sources for prompts, budget-aware (see source_formatting)."""
        return format_sources_for_synthesis(
            sources,
            input_budget_tokens,
            guidance=guidance,
            contradiction_notes=contradiction_notes,
        )

    async def _call_llm(
        self,
        prompt: str,
        max_tokens: int = 1000,
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
