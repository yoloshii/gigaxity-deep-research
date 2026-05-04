"""
Enhanced Multi-Stage Synthesis

Addresses Gap #4 and #5: Single-Pass Synthesis and Weak Citation Binding.

Multi-stage approach:
1. Outline generation
2. Section drafting (parallel)
3. Citation binding (claim-to-evidence)
4. Coherence refinement
5. Self-evaluation

Based on research:
- "Self-RAG: Learning to Retrieve, Generate, and Critique" (Asai et al.)
- "Chain-of-Thought Prompting" (Wei et al.)
"""

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config import settings
from ..connectors.base import Source
from ..llm_utils import get_llm_content
from ..ranking.passage import Passage


class SynthesisDepth(str, Enum):
    """Depth of synthesis."""
    BRIEF = "brief"           # Quick answer, 1-2 paragraphs
    MEDIUM = "medium"         # Standard response, 3-5 paragraphs
    COMPREHENSIVE = "comprehensive"  # Full analysis, structured sections


@dataclass
class SynthesisResult:
    """Result of enhanced synthesis."""
    content: str
    citations: list[dict]
    confidence: float
    coverage: float  # How much of the query was addressed
    methodology: str  # What approach was used
    trace: list[dict] = field(default_factory=list)  # Debugging trace


@dataclass
class ClaimBinding:
    """Binding between a claim and its evidence."""
    claim: str
    evidence_passage: Optional[str]
    source_id: str
    confidence: float
    needs_verification: bool = False


class EnhancedSynthesizer:
    """
    Multi-stage synthesis engine with claim-level citation binding.

    Key improvements over basic synthesis:
    1. Structured outline before writing
    2. Parallel section generation
    3. Explicit claim-to-evidence binding
    4. Self-evaluation with confidence scores
    """

    # Prompts for each stage
    OUTLINE_PROMPT = """You are creating an outline for a research synthesis.

Query: {query}

Available sources (with relevant passages):
{sources}

Create a structured outline with 3-5 main sections that will best answer the query.
For each section, note which sources are most relevant.

Format:
## Section 1: [Title]
- Key points to cover
- Relevant sources: [source_ids]

## Section 2: [Title]
...

Keep the outline focused and directly relevant to answering the query."""

    SECTION_PROMPT = """Write the "{section_title}" section of a research synthesis.

Query context: {query}

Section requirements:
{section_requirements}

Relevant source passages:
{passages}

Instructions:
1. Write 2-4 paragraphs for this section
2. Include inline citations using [source_id] format
3. Every factual claim MUST have a citation
4. Synthesize information, don't just summarize each source
5. Be accurate - only cite sources that actually support the claim

Write the section now:"""

    CITATION_BINDING_PROMPT = """Analyze this synthesis text and verify each claim has proper evidence.

Text:
{text}

Available evidence passages:
{passages}

For each factual claim in the text:
1. Identify the claim
2. Find supporting evidence from passages
3. Verify the citation is accurate
4. Flag any unsupported claims

Output format:
CLAIM: [the claim]
EVIDENCE: [passage excerpt] from [source_id]
CONFIDENCE: [high/medium/low]
---

List all claims:"""

    REFINEMENT_PROMPT = """Refine this research synthesis for clarity and coherence.

Original query: {query}

Draft synthesis:
{draft}

Issues to address:
{issues}

Instructions:
1. Fix any coherence issues between sections
2. Ensure smooth transitions
3. Remove redundancy
4. Verify introduction matches conclusion
5. Keep all citations intact

Output the refined synthesis:"""

    EVALUATION_PROMPT = """Evaluate this research synthesis.

Query: {query}

Synthesis:
{synthesis}

Evaluate on these criteria (score 1-10):
1. COMPLETENESS: Does it fully answer the query?
2. ACCURACY: Are claims well-supported by citations?
3. COHERENCE: Is it well-organized and logical?
4. DEPTH: Is the analysis sufficiently detailed?

Output format:
COMPLETENESS: [score] - [brief reason]
ACCURACY: [score] - [brief reason]
COHERENCE: [score] - [brief reason]
DEPTH: [score] - [brief reason]
OVERALL: [average score]
CONFIDENCE: [high/medium/low based on source quality]
COVERAGE: [percentage of query addressed]"""

    def __init__(
        self,
        llm_client,
        fast_llm_client=None,
        model: str = None,
        fast_model: str = None,
    ):
        """
        Initialize the enhanced synthesizer.

        Args:
            llm_client: Primary LLM client (OpenAI-compatible)
            fast_llm_client: Optional faster model for outline/eval
            model: Model name for primary synthesis
            fast_model: Model name for fast operations
        """
        self.llm_client = llm_client
        self.fast_llm_client = fast_llm_client or llm_client
        self.model = model or settings.llm_model
        self.fast_model = fast_model or self.model

    async def synthesize(
        self,
        query: str,
        sources: list[Source],
        passages: Optional[list[Passage]] = None,
        depth: SynthesisDepth = SynthesisDepth.MEDIUM,
        skip_evaluation: bool = False,
    ) -> SynthesisResult:
        """
        Perform multi-stage synthesis.

        Args:
            query: The research query
            sources: Source documents
            passages: Optional pre-extracted passages
            depth: Synthesis depth
            skip_evaluation: Skip self-evaluation (faster)

        Returns:
            SynthesisResult with content and metadata
        """
        trace = []

        # Stage 1: Generate outline
        outline = await self._generate_outline(query, sources, passages)
        trace.append({"stage": "outline", "output": outline})

        # Stage 2: Draft sections (parallel)
        sections = await self._draft_sections(query, outline, sources, passages)
        trace.append({"stage": "sections", "output": sections})

        # Stage 3: Combine and bind citations
        draft = self._combine_sections(sections)
        bindings = await self._bind_citations(draft, sources, passages)
        trace.append({"stage": "citation_binding", "bindings": len(bindings)})

        # Stage 4: Refine for coherence
        issues = self._identify_issues(draft, bindings)
        refined = await self._refine(query, draft, issues)
        trace.append({"stage": "refinement", "issues_fixed": len(issues)})

        # Stage 5: Self-evaluation
        if not skip_evaluation:
            evaluation = await self._evaluate(query, refined)
            trace.append({"stage": "evaluation", "output": evaluation})
            confidence = evaluation.get("confidence", 0.7)
            coverage = evaluation.get("coverage", 0.8)
        else:
            confidence = 0.7
            coverage = 0.8

        # Extract citations from final text
        citations = self._extract_citations(refined, sources)

        return SynthesisResult(
            content=refined,
            citations=citations,
            confidence=confidence,
            coverage=coverage,
            methodology="multi-stage-synthesis",
            trace=trace,
        )

    async def _generate_outline(
        self,
        query: str,
        sources: list[Source],
        passages: Optional[list[Passage]],
    ) -> dict:
        """Generate synthesis outline."""
        # Format sources for prompt
        source_text = self._format_sources_for_prompt(sources, passages)

        prompt = self.OUTLINE_PROMPT.format(
            query=query,
            sources=source_text
        )

        response = await self._call_llm(
            prompt,
            client=self.fast_llm_client,
            model=self.fast_model,
            max_tokens=1000,
        )

        # Parse outline into sections
        sections = self._parse_outline(response)

        return {
            "raw": response,
            "sections": sections,
        }

    async def _draft_sections(
        self,
        query: str,
        outline: dict,
        sources: list[Source],
        passages: Optional[list[Passage]],
    ) -> list[dict]:
        """Draft each section in parallel."""
        sections = outline.get("sections", [])

        if not sections:
            # Fallback to single-pass if outline failed
            return [await self._draft_single(query, sources, passages)]

        # Create tasks for parallel execution
        tasks = []
        for section in sections:
            task = self._draft_section(
                query=query,
                section_title=section.get("title", ""),
                section_requirements=section.get("points", ""),
                relevant_source_ids=section.get("sources", []),
                all_sources=sources,
                passages=passages,
            )
            tasks.append(task)

        # Execute in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any failures
        drafted = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                drafted.append({
                    "title": sections[i].get("title", f"Section {i+1}"),
                    "content": f"[Error drafting section: {result}]",
                    "error": True,
                })
            else:
                drafted.append(result)

        return drafted

    async def _draft_section(
        self,
        query: str,
        section_title: str,
        section_requirements: str,
        relevant_source_ids: list[str],
        all_sources: list[Source],
        passages: Optional[list[Passage]],
    ) -> dict:
        """Draft a single section."""
        # Filter to relevant sources/passages
        relevant_sources = [s for s in all_sources if s.id in relevant_source_ids]
        if not relevant_sources:
            relevant_sources = all_sources[:5]  # Fallback

        if passages:
            relevant_passages = [
                p for p in passages
                if p.source_id in relevant_source_ids
            ]
            if not relevant_passages:
                relevant_passages = passages[:10]
            passage_text = self._format_passages_for_prompt(relevant_passages)
        else:
            passage_text = self._format_sources_for_prompt(relevant_sources)

        prompt = self.SECTION_PROMPT.format(
            section_title=section_title,
            query=query,
            section_requirements=section_requirements,
            passages=passage_text,
        )

        response = await self._call_llm(
            prompt,
            max_tokens=1500,
        )

        return {
            "title": section_title,
            "content": response,
            "sources_used": [s.id for s in relevant_sources],
        }

    async def _draft_single(
        self,
        query: str,
        sources: list[Source],
        passages: Optional[list[Passage]],
    ) -> dict:
        """Fallback single-pass draft."""
        # Use basic synthesis
        from .prompts import build_research_prompt
        prompt = build_research_prompt(query, sources)

        response = await self._call_llm(prompt, max_tokens=3000)

        return {
            "title": "Response",
            "content": response,
            "sources_used": [s.id for s in sources],
        }

    def _combine_sections(self, sections: list[dict]) -> str:
        """Combine sections into full draft."""
        parts = []
        for section in sections:
            if section.get("error"):
                continue
            title = section.get("title", "")
            content = section.get("content", "")
            if title and content:
                parts.append(f"## {title}\n\n{content}")
            elif content:
                parts.append(content)

        return "\n\n".join(parts)

    async def _bind_citations(
        self,
        draft: str,
        sources: list[Source],
        passages: Optional[list[Passage]],
    ) -> list[ClaimBinding]:
        """Bind claims to evidence with verification."""
        # This is a simplified version - full implementation would
        # use embedding similarity for claim-passage matching

        bindings = []
        source_map = {s.id: s for s in sources}

        # Extract existing citations
        citation_pattern = re.compile(r'\[([^\]]+)\]')
        matches = citation_pattern.findall(draft)

        for citation_id in matches:
            if citation_id in source_map:
                bindings.append(ClaimBinding(
                    claim="[extracted from context]",
                    evidence_passage=source_map[citation_id].content[:200] if source_map[citation_id].content else "",
                    source_id=citation_id,
                    confidence=0.8,  # Assume high confidence if cited
                    needs_verification=False,
                ))
            else:
                bindings.append(ClaimBinding(
                    claim="[extracted from context]",
                    evidence_passage=None,
                    source_id=citation_id,
                    confidence=0.0,
                    needs_verification=True,
                ))

        return bindings

    def _identify_issues(self, draft: str, bindings: list[ClaimBinding]) -> list[str]:
        """Identify issues in the draft."""
        issues = []

        # Check for unverified citations
        unverified = [b for b in bindings if b.needs_verification]
        if unverified:
            issues.append(f"Found {len(unverified)} unverified citations")

        # Check for very short sections
        sections = draft.split("##")
        for section in sections:
            if section.strip() and len(section.strip()) < 100:
                issues.append("Found very short section")

        # Check for missing conclusion indicators
        if "conclusion" not in draft.lower() and "summary" not in draft.lower():
            if len(draft) > 1000:
                issues.append("Missing conclusion/summary")

        return issues

    async def _refine(self, query: str, draft: str, issues: list[str]) -> str:
        """Refine draft for coherence."""
        if not issues:
            return draft  # No refinement needed

        prompt = self.REFINEMENT_PROMPT.format(
            query=query,
            draft=draft,
            issues="\n".join(f"- {issue}" for issue in issues),
        )

        response = await self._call_llm(
            prompt,
            client=self.fast_llm_client,
            model=self.fast_model,
            max_tokens=4000,
        )

        return response

    async def _evaluate(self, query: str, synthesis: str) -> dict:
        """Self-evaluate the synthesis."""
        prompt = self.EVALUATION_PROMPT.format(
            query=query,
            synthesis=synthesis,
        )

        response = await self._call_llm(
            prompt,
            client=self.fast_llm_client,
            model=self.fast_model,
            max_tokens=500,
        )

        # Parse evaluation
        return self._parse_evaluation(response)

    def _parse_outline(self, outline_text: str) -> list[dict]:
        """Parse outline text into structured sections."""
        sections = []
        current_section = None

        for line in outline_text.split("\n"):
            line = line.strip()
            if line.startswith("## "):
                if current_section:
                    sections.append(current_section)
                title = line[3:].strip()
                # Remove "Section N:" prefix if present
                title = re.sub(r'^Section\s+\d+:\s*', '', title)
                current_section = {"title": title, "points": [], "sources": []}
            elif current_section:
                if line.startswith("- "):
                    point = line[2:]
                    if "Relevant sources:" in point:
                        # Extract source IDs
                        sources_part = point.split("Relevant sources:")[-1]
                        source_ids = re.findall(r'[\w_]+', sources_part)
                        current_section["sources"].extend(source_ids)
                    else:
                        current_section["points"].append(point)

        if current_section:
            sections.append(current_section)

        return sections

    def _parse_evaluation(self, eval_text: str) -> dict:
        """Parse evaluation response."""
        result = {"confidence": 0.7, "coverage": 0.8}

        # Extract overall score
        overall_match = re.search(r'OVERALL:\s*(\d+(?:\.\d+)?)', eval_text)
        if overall_match:
            score = float(overall_match.group(1))
            result["overall_score"] = score / 10  # Normalize to 0-1

        # Extract confidence
        conf_match = re.search(r'CONFIDENCE:\s*(high|medium|low)', eval_text, re.I)
        if conf_match:
            conf_map = {"high": 0.9, "medium": 0.7, "low": 0.4}
            result["confidence"] = conf_map.get(conf_match.group(1).lower(), 0.7)

        # Extract coverage
        cov_match = re.search(r'COVERAGE:\s*(\d+)%?', eval_text)
        if cov_match:
            result["coverage"] = int(cov_match.group(1)) / 100

        return result

    def _format_sources_for_prompt(
        self,
        sources: list[Source],
        passages: Optional[list[Passage]] = None,
        max_length: int = 8000,
    ) -> str:
        """Format sources for inclusion in prompts."""
        parts = []
        total_length = 0

        for source in sources:
            source_text = f"[{source.id}] {source.title}\nURL: {source.url}\n"

            # Use passages if available
            if passages:
                source_passages = [p for p in passages if p.source_id == source.id]
                for p in source_passages[:2]:  # Max 2 passages per source
                    source_text += f"Passage: {p.text[:500]}...\n"
            elif source.content:
                source_text += f"Content: {source.content[:400]}...\n"

            if total_length + len(source_text) > max_length:
                break

            parts.append(source_text)
            total_length += len(source_text)

        return "\n---\n".join(parts)

    def _format_passages_for_prompt(
        self,
        passages: list[Passage],
        max_length: int = 6000,
    ) -> str:
        """Format passages for inclusion in prompts."""
        parts = []
        total_length = 0

        for passage in passages:
            passage_text = f"[{passage.source_id}] From: {passage.source_title}\n{passage.text}\n"

            if total_length + len(passage_text) > max_length:
                break

            parts.append(passage_text)
            total_length += len(passage_text)

        return "\n---\n".join(parts)

    def _extract_citations(self, text: str, sources: list[Source]) -> list[dict]:
        """Extract citations from final text."""
        source_map = {s.id: s for s in sources}
        citations = []
        seen = set()

        pattern = re.compile(r'\[([^\]]+)\]')
        for match in pattern.finditer(text):
            source_id = match.group(1)
            if source_id in source_map and source_id not in seen:
                source = source_map[source_id]
                citations.append({
                    "id": source_id,
                    "title": source.title,
                    "url": source.url,
                })
                seen.add(source_id)

        return citations

    async def _call_llm(
        self,
        prompt: str,
        client=None,
        model: str = None,
        max_tokens: int = 2000,
    ) -> str:
        """Call LLM with prompt."""
        client = client or self.llm_client
        model = model or self.model

        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.7,
        )

        return get_llm_content(response.choices[0].message)
