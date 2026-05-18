"""
Source Quality Gate

Research basis: CRAG - Corrective RAG (arXiv:2401.15884)
- Evaluate retrieved evidence quality BEFORE generation
- If quality is low, reject and suggest better queries
- Prevents hallucination from irrelevant sources

Key insight: Average relevance score < 0.3 = reject synthesis.
Suggest additional searches to fill gaps.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import LLMOutput, ExtractionMode, call_with_extraction
from .entity_allowlist import (
    CONTEXT_CUES,
    CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST,
    LOWERCASE_TOOL_ALLOWLIST,
)


# Common query-shape words that look like proper nouns to the capitalized-token
# heuristic but aren't entities. Anything in here is dropped from the candidate
# entity list before entity-balanced promotion.
_QUERY_ENTITY_STOPWORDS = {
    "Compare", "Comparing", "Compares", "Comparison",
    "The", "A", "An",
    "What", "How", "When", "Where", "Why", "Which", "Who", "Whose",
    "And", "Or", "But", "For", "With", "Without", "About", "From", "Into",
    "API", "APIs", "SDK", "SDKs", "URL", "URLs",
    "AI", "ML", "LLM", "LLMs",
    "May", "June", "July", "August", "September", "October", "November",
    "December", "January", "February", "March", "April",
}


def _entity_match_count(text_lower: str, entity_lower: str) -> int:
    """Count token-boundary occurrences of ``entity_lower`` in ``text_lower``.

    Uses ``\\b`` regex boundaries so ``Exa`` does not match ``example`` and
    ``Rust`` does not match ``trustworthy``. Both arguments are expected to
    be pre-lowercased — re.IGNORECASE is NOT applied because callers
    already lowercase for cache-locality reasons.

    Shared by ``SourceQualityGate._entity_centrality()`` (which previously
    used substring ``in`` + ``str.count``, the failure mode codex T3F1
    flagged) and by the post-synthesis verifier's entity-coverage check.
    """
    if not entity_lower or not text_lower:
        return 0
    # re.escape so entity-name punctuation (gpt-4o, llama.cpp) is matched
    # literally. The trailing \b after the escaped entity ensures "exa" does
    # not match "exa-mple" via the leading boundary alone.
    pattern = r"\b" + re.escape(entity_lower) + r"\b"
    return len(re.findall(pattern, text_lower))


def _entity_in_text(text_lower: str, entity_lower: str) -> bool:
    """Token-boundary presence check — True iff ``entity_lower`` appears as
    a standalone token (per ``_entity_match_count``) in ``text_lower``."""
    return _entity_match_count(text_lower, entity_lower) > 0


def _split_phrase_at_stopwords(phrase: str) -> list[str]:
    """Split a multi-word capitalized phrase at stopword boundaries.

    The regex grabs consecutive capitalized words greedily ("Compare Tavily",
    "Serper APIs"), but the actual entity is the non-stopword core. Splitting
    at stopword boundaries recovers ["Tavily"] from "Compare Tavily" and
    ["Serper"] from "Serper APIs".
    """
    parts = phrase.split()
    result: list[str] = []
    current: list[str] = []
    for p in parts:
        if p in _QUERY_ENTITY_STOPWORDS:
            if current:
                result.append(" ".join(current))
                current = []
        else:
            current.append(p)
    if current:
        result.append(" ".join(current))
    return result


def extract_query_entities(query: str) -> list[str]:
    """Extract candidate named entities from a query.

    Heuristic: regex matches across five shapes that cover the dominant
    technology-name patterns:

    1. Capitalized words (3+ chars): ``Tavily`` / ``LinkUp`` / ``FastAPI`` /
       ``Postgres``. Greedy matches that include leading/trailing stopwords
       ("Compare Tavily", "Serper APIs") get split at the stopword boundary
       so the entity core survives.
    2. Internal-cap identifiers: ``vLLM`` / ``iOS`` / ``eBay`` / ``wxWidgets``
       — lowercase start with at least one internal uppercase. Common in
       ML/dev tool naming.
    3. Hyphenated identifiers with caps or digits: ``gpt-4o`` / ``claude-3-5``
       / ``Llama-3`` / ``TypeScript-2``. Requires at least one hyphen and
       a leading letter.
    4. Dotted module paths: ``llama.cpp`` / ``asyncio.gather`` /
       ``numpy.array``. Requires a dot between identifier-shaped tokens.
    5. Curated lowercase tools (``bun`` / ``npm`` / ``deno`` / ``pnpm`` /
       ``pip``): matched against ``LOWERCASE_TOOL_ALLOWLIST`` exactly,
       case-sensitive. Ambiguous tokens (``go`` / ``rust`` / ``uv``) in
       ``CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST`` only fire when the query
       carries technical/comparison context cues OR shapes 1-4 already
       extracted at least one tech entity. Lowercase candidates inside
       hyphenated/dotted matches (``pip`` inside ``pip-tools``) are NOT
       re-emitted — the hyphenated form is the canonical entity.

    Returns deduplicated, order-preserving list. Stopword filter applied
    to shape (1). Shapes (2)–(5) are inherently entity-shaped (or curated)
    so the stopword filter is skipped for them.

    KNOWN LIMITATIONS:
    - Non-English entity names (CJK, Cyrillic, etc.) are NOT detected.
    - Numeric-only identifiers (``2026``, ``v3``) are NOT detected.
    - Acronyms shorter than 3 chars (``AI``, ``ML``, ``UI``) are NOT
      detected — by design, to avoid noise.
    - Lowercase tools outside the curated allowlist are NOT detected;
      maintainers extend ``entity_allowlist.py`` to add coverage.
    """
    if not query:
        return []
    # Shape 1: Capitalized words (existing behavior — kept first for
    # priority + stopword stripping). Length >= 3 to avoid catching
    # interrogatives + acronyms.
    cap_pattern = r"\b[A-Z][a-zA-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z][a-zA-Z]+)*\b"
    # Shape 2: lowercase-start with internal uppercase (vLLM, iOS, eBay).
    internal_cap_pattern = r"\b[a-z][a-z]*[A-Z][a-zA-Z]+\b"
    # Shape 3: hyphenated identifiers — letter start, at least one hyphen,
    # alphanumeric segments thereafter.
    hyphenated_pattern = r"\b[A-Za-z][a-zA-Z0-9]*(?:-[a-zA-Z0-9]+){1,3}\b"
    # Shape 4: dotted module paths — letter start, at least one dot between
    # identifier-shaped tokens.
    dotted_pattern = r"\b[a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]+)+\b"

    seen: set[str] = set()
    result: list[str] = []
    # Tracks ONLY shapes 2-4 entities — used to enable the Shape 5
    # contextual tier without misreading Shape 1 proper nouns (Bob,
    # Alice, Taylor Swift) as tech entities. Codex T10 HIGH (NONCE
    # codex-design-items-6-7-2026-05-18-7e3a9c4b): `bool(result)`
    # let "What did Bob make for dinner?" extract `make` because Bob
    # enabled the contextual tier. Shapes 2-4 are inherently tech-
    # shaped, so they are the correct signal.
    tech_shaped_entities: list[str] = []

    # Shape 1 — split at stopword boundaries
    for c in re.findall(cap_pattern, query):
        for sub in _split_phrase_at_stopwords(c):
            if not sub or sub in seen:
                continue
            seen.add(sub)
            result.append(sub)

    # Shapes 2-4 — no stopword filter (these patterns are inherently entity-
    # shaped; "vLLM" / "gpt-4o" / "llama.cpp" never collide with English
    # stopwords).
    for pattern in (internal_cap_pattern, hyphenated_pattern, dotted_pattern):
        for c in re.findall(pattern, query):
            if c not in seen:
                seen.add(c)
                result.append(c)
                tech_shaped_entities.append(c)

    # Shape 5 — curated lowercase tool allowlist (Item 7, post-v0.3.0).
    # Contextual tier is enabled only when (a) a CONTEXT_CUES word appears
    # in the query OR (b) shapes 2-4 (NOT shape 1) already found a
    # tech-shaped entity — codex design Q9 + T10 HIGH refinement. Prevents
    # "remove rust from metal" / "how to go faster" / "What did Bob make
    # for dinner?" from being treated as Rust / Go / Make queries.
    query_lower_words = set(re.findall(r"\b[a-z]+\b", query.lower()))
    contextual_enabled = (
        bool(query_lower_words & CONTEXT_CUES)
        or bool(tech_shaped_entities)
    )
    allowlist = LOWERCASE_TOOL_ALLOWLIST
    if contextual_enabled:
        allowlist = allowlist | CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST
    # Token boundary excludes letters, digits, dots, hyphens, underscores
    # on both sides — so `pip` inside `pip-tools` / `numpy.pip` / `pip_tools`
    # does NOT re-emit (codex design Q11; shape 3/4 already cover those).
    # Pattern is case-sensitive (no re.IGNORECASE), so `PIP` in the query
    # does not lowercase-fold to `pip` (codex design Q10).
    existing_lower = {e.lower() for e in result}
    for match in re.finditer(r"(?<![A-Za-z0-9_.\-])[a-z]+(?![A-Za-z0-9_.\-])", query):
        token = match.group(0)
        if token not in allowlist:
            continue
        if token in seen or token in existing_lower:
            continue
        seen.add(token)
        existing_lower.add(token)
        result.append(token)

    return result


class QualityDecision(str, Enum):
    """Decision outcome from quality gate."""
    PROCEED = "proceed"  # Sources adequate, continue synthesis
    REJECT = "reject"    # Sources inadequate, suggest alternatives
    PARTIAL = "partial"  # Some sources good, filter and continue


@dataclass
class QualityGateResult:
    """Result of quality gate evaluation."""
    decision: QualityDecision
    avg_quality: float
    good_sources: list  # Sources that pass threshold
    rejected_sources: list  # Sources below threshold
    source_scores: list[float] = None  # Individual scores
    suggestion: Optional[str] = None  # Suggested additional searches
    reason: Optional[str] = None


class SourceQualityGate:
    """
    Evaluate source quality and decide whether to proceed with synthesis.

    Usage:
        gate = SourceQualityGate(llm_client)
        result = await gate.evaluate(query, sources)

        if result.decision == QualityDecision.REJECT:
            return {"status": "insufficient_sources", "suggestion": result.suggestion}
        elif result.decision == QualityDecision.PARTIAL:
            sources = result.good_sources  # Use filtered sources
    """

    # Implementation defaults inspired by CRAG (arXiv:2401.15884). The paper
    # demonstrates the three-bucket schema (PASS / PARTIAL / REJECT) but does
    # not prescribe exact thresholds; these values are tuned against the
    # bundled `comprehensive` and `fast` presets and may need adjustment for
    # other domains. Override per-instance if your source distribution shifts.
    REJECT_THRESHOLD = 0.3  # Below this, reject entirely
    PASS_THRESHOLD = 0.5    # Above this, source is good

    SCORING_PROMPT = """Rate each source's relevance to the query (0.0 to 1.0).

Query: {query}

Sources:
{sources}

For each source, provide a score:
- 1.0 = Directly answers the query
- 0.7 = Highly relevant context
- 0.5 = Somewhat relevant
- 0.3 = Tangentially related
- 0.0 = Completely irrelevant

Format: One score per line, just the number (e.g., 0.8)."""

    SUGGESTION_PROMPT = """The following sources don't adequately cover this query.

Query: {query}

Current sources cover:
{coverage}

What additional searches would help answer this query?
Provide 2-3 specific search queries that would fill gaps.

Format: One query per line."""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
        reject_threshold: float = None,
        pass_threshold: float = None,
        entity_balanced: bool = False,
    ):
        """
        Initialize quality gate.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for LLM calls
            reject_threshold: Avg score below which to reject (default 0.3)
            pass_threshold: Individual source threshold (default 0.5)
            entity_balanced: When True, after the scalar gate runs, promote the
                highest-scoring rejected source for each capitalized query
                entity that is not represented in good_sources. Prevents
                whole-vendor blackouts on multi-entity comparison queries
                while still filtering genuinely irrelevant sources. Only
                activates when the query has 2+ entities and the gate
                decision is PARTIAL.
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model
        self.reject_threshold = reject_threshold or self.REJECT_THRESHOLD
        self.pass_threshold = pass_threshold or self.PASS_THRESHOLD
        self.entity_balanced = entity_balanced

    async def evaluate(
        self,
        query: str,
        sources: list,
    ) -> QualityGateResult:
        """
        Evaluate source quality for query.

        Args:
            query: The research query
            sources: Pre-gathered sources to evaluate

        Returns:
            QualityGateResult with decision and filtered sources
        """
        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided. Try searching with broader terms.",
                reason="No sources to evaluate",
            )

        # Score each source for query relevance
        if self.llm_client:
            scores = await self._score_sources_llm(query, sources)
        else:
            scores = self._score_sources_heuristic(query, sources)

        avg_quality = sum(scores) / len(scores)

        # Categorize sources
        good_sources = []
        rejected_with_scores: list[tuple[object, float]] = []
        for source, score in zip(sources, scores):
            if score >= self.pass_threshold:
                good_sources.append(source)
            else:
                rejected_with_scores.append((source, score))

        # Decide first whether the whole source set is too weak to rescue.
        # REJECT is governed by avg_quality across ALL sources, so
        # entity-balanced promotion cannot override it — if the average is
        # below the floor, the set is genuinely irrelevant.
        if avg_quality < self.reject_threshold:
            suggestion = await self._suggest_searches(query, sources) if self.llm_client else \
                f"Try more specific searches related to: {query}"
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=avg_quality,
                good_sources=[],
                rejected_sources=sources,
                source_scores=scores,
                suggestion=suggestion,
                reason=f"Average relevance {avg_quality:.2f} below threshold {self.reject_threshold}",
            )

        # Entity-balanced safety net: when enabled and the query names 2+
        # capitalized entities, ensure each entity is represented by at least
        # one source. Multi-vendor comparison queries score per-vendor sources
        # at ~0.4–0.5 under the scalar scorer, which can blackout an entire
        # vendor if its sources all land below pass_threshold.
        #
        # Promotion uses entity-CENTRALITY scoring, not just substring
        # presence (Turn 2 codex F4): a LinkUp-focused source that says
        # "unlike Tavily" once should NOT be promoted as Tavily coverage.
        # Tiered signals: title match (3.0) > dense body mentions (1.0+
        # density bonus capped at 2.0) > single body mention (1.0) > 0.
        # Ties break by scalar relevance score.
        if self.entity_balanced and rejected_with_scores:
            entities = extract_query_entities(query)
            if len(entities) >= 2:
                promoted_set: set[int] = set()
                for entity in entities:
                    entity_lower = entity.lower()
                    # "Already covered" check uses centrality (Turn 2 codex F4):
                    # an incidental one-off body mention does NOT count as
                    # coverage. Require some good source to have centrality
                    # >= 2.0 for the entity (title match OR 3+ body mentions).
                    if any(
                        self._entity_centrality(s, entity_lower) >= 2.0
                        for s in good_sources
                    ):
                        continue
                    # Score every still-rejected source by entity-centrality.
                    # Turn 3 codex recommendation #2: tightened promotion
                    # threshold from `> 0` to `>= 2.0`. A source with a
                    # single off-hand mention of the entity ("unlike Tavily,
                    # LinkUp...") is NOT meaningful coverage of that entity
                    # — promoting it pollutes the synthesis input. Cleaner
                    # failure: entity stays uncovered → verifier hard-fails
                    # if the synthesis discusses it → operator gathers
                    # better sources.
                    candidates: list[tuple[int, object, float, float]] = []
                    for idx, (src, score) in enumerate(rejected_with_scores):
                        if idx in promoted_set:
                            continue
                        centrality = self._entity_centrality(src, entity_lower)
                        if centrality >= 2.0:
                            candidates.append((idx, src, centrality, score))
                    if not candidates:
                        continue
                    # Highest centrality wins; scalar score breaks ties.
                    candidates.sort(key=lambda c: (c[2], c[3]), reverse=True)
                    chosen_idx, chosen_src, _, _ = candidates[0]
                    good_sources.append(chosen_src)
                    promoted_set.add(chosen_idx)
                # Rebuild rejected list excluding promoted indices.
                rejected_with_scores = [
                    pair
                    for idx, pair in enumerate(rejected_with_scores)
                    if idx not in promoted_set
                ]

        rejected_sources = [s for s, _ in rejected_with_scores]

        if len(good_sources) < len(sources):
            return QualityGateResult(
                decision=QualityDecision.PARTIAL,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=rejected_sources,
                source_scores=scores,
                reason=f"Filtered {len(rejected_sources)} low-quality sources",
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
            )

    async def _score_sources_llm(
        self,
        query: str,
        sources: list,
    ) -> list[float]:
        """Score sources using LLM."""
        prompt = self.SCORING_PROMPT.format(
            query=query,
            sources=self._format_sources(sources),
        )

        try:
            output = await self._call_llm(prompt, mode=ExtractionMode.PARSE_REQUIRED)
        except Exception:
            # Network/transport error - fall back to the deterministic heuristic.
            return self._score_sources_heuristic(query, sources)

        scores = self._parse_scores(output.text)
        # PARSE_REQUIRED: a valid parse yields exactly one score per source.
        # A short, over-long, or otherwise wrong count means the structured
        # response was not understood - fall back to the heuristic rather than
        # synthesizing over 0.5-padded guesses.
        if len(scores) != len(sources):
            return self._score_sources_heuristic(query, sources)

        return scores

    def _score_sources_heuristic(
        self,
        query: str,
        sources: list,
    ) -> list[float]:
        """Score sources using keyword overlap heuristic."""
        query_terms = set(
            word.lower() for word in re.findall(r'\b\w+\b', query)
            if len(word) > 3
        )

        if not query_terms:
            return [0.5] * len(sources)

        scores = []
        for source in sources:
            content = self._get_content(source)
            content_lower = content.lower()

            # Count term matches
            matches = sum(1 for term in query_terms if term in content_lower)
            overlap_ratio = matches / len(query_terms)

            # Scale to 0-1
            score = min(overlap_ratio * 1.2, 1.0)  # Slight boost
            scores.append(score)

        return scores

    def _parse_scores(self, response: str) -> list[float]:
        """Parse scores from an LLM response, one score per non-empty line.

        Returns exactly the scores it could parse - no padding, no truncation.
        The caller compares the count against the source count to decide
        whether the structured response was understood.
        """
        scores = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                # Extract number from line (handles "1. 0.8" or "0.8" formats)
                numbers = re.findall(r'\d+\.?\d*', line)
                if numbers:
                    score = float(numbers[-1])  # Take last number
                    scores.append(min(max(score, 0.0), 1.0))
            except ValueError:
                continue

        return scores

    async def _suggest_searches(
        self,
        query: str,
        sources: list,
    ) -> str:
        """Suggest additional searches to improve coverage."""
        coverage = "\n".join(
            f"- {self._get_title(s)}"
            for s in sources[:5]
        )

        prompt = self.SUGGESTION_PROMPT.format(
            query=query,
            coverage=coverage,
        )

        try:
            output = await self._call_llm(prompt, mode=ExtractionMode.LENIENT)
            return f"Consider searching for: {output.text.strip()}"
        except Exception:
            return f"Try more specific searches related to: {query}"

    # Content window for LLM scoring. 300 chars (the original CRAG-paper-inspired
    # default) is too tight for sources where the relevant evidence appears later
    # — multi-vendor comparison queries especially suffer, since each source covers
    # only one entity and the entity name may not appear in the first 300 chars.
    # 1500 chars covers a typical lead paragraph plus subhead context.
    _SCORING_CONTENT_WINDOW = 1500

    def _format_sources(self, sources: list) -> str:
        """Format sources for scoring prompt."""
        parts = []
        for i, s in enumerate(sources, 1):
            title = self._get_title(s)
            content = self._get_content(s)[: self._SCORING_CONTENT_WINDOW]
            parts.append(f"[{i}] {title}\n{content}...")
        return "\n\n".join(parts)

    def _entity_centrality(self, source, entity_lower: str) -> float:
        """Score how central an entity is to a source. Higher = better
        promotion candidate.

        Tiered (Turn 2 codex F4 + Turn 3 codex T3F1 boundary fix):
        - Title token match → 3.0 (clearest signal of source-entity binding).
        - Body token mentions → 1.0 base + 0.5 per additional mention,
          capped at 3.0 (dense coverage beats incidental one-off mention).
        - No title token match AND zero body token mentions → 0.0 (skip
          promotion).

        Uses ``\\b``-bounded matching via ``_entity_match_count`` — substring
        matching would let ``Exa`` collide with ``example`` and inflate
        centrality from unrelated text. T3F1 fix.
        """
        title_lower = self._get_title(source).lower()
        content_lower = self._get_content(source).lower()
        if _entity_in_text(title_lower, entity_lower):
            return 3.0
        mentions = _entity_match_count(content_lower, entity_lower)
        if mentions == 0:
            return 0.0
        # 1 mention = 1.0; 2 = 1.5; 3 = 2.0; ≥5 → 3.0 (cap)
        return min(1.0 + 0.5 * (mentions - 1), 3.0)

    def _get_title(self, source) -> str:
        """Extract title from source."""
        if hasattr(source, 'title'):
            return source.title or "Untitled"
        return "Untitled"

    def _get_content(self, source) -> str:
        """Extract content from source."""
        if hasattr(source, 'content'):
            return source.content or ""
        if hasattr(source, 'text'):
            return source.text or ""
        if isinstance(source, str):
            return source
        return ""

    async def _call_llm(
        self,
        prompt: str,
        max_tokens: int = 500,
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
            temperature=0.3,
        )

    def evaluate_sync(
        self,
        query: str,
        sources: list,
    ) -> QualityGateResult:
        """
        Synchronous evaluation using heuristics only.

        Useful for quick evaluation without async overhead.
        """
        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided.",
                reason="No sources to evaluate",
            )

        scores = self._score_sources_heuristic(query, sources)
        avg_quality = sum(scores) / len(scores)

        good_sources = []
        rejected_sources = []
        for source, score in zip(sources, scores):
            if score >= self.pass_threshold:
                good_sources.append(source)
            else:
                rejected_sources.append(source)

        if avg_quality < self.reject_threshold:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=avg_quality,
                good_sources=[],
                rejected_sources=sources,
                source_scores=scores,
                suggestion=f"Try more specific searches related to: {query}",
                reason=f"Average relevance {avg_quality:.2f} below threshold",
            )
        elif len(good_sources) < len(sources):
            return QualityGateResult(
                decision=QualityDecision.PARTIAL,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=rejected_sources,
                source_scores=scores,
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
            )
