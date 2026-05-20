"""
Source Quality Gate

Research basis: CRAG - Corrective RAG (arXiv:2401.15884)
- Evaluate retrieved evidence quality BEFORE generation
- If quality is low, reject and suggest better queries
- Prevents hallucination from irrelevant sources

Key insight: Average relevance score < 0.3 = reject synthesis.
Suggest additional searches to fill gaps.
"""

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import LLMOutput, ExtractionMode, call_with_extraction, is_reasoning_model
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


# Lowercase function words dropped from the keyword-scoring term set (Q1a).
# Distinct from _QUERY_ENTITY_STOPWORDS (entity extraction): scoring needs a
# broad stop set so a verbose brief's function words ("between", "without",
# "through") don't enter the matched-term count and dilute it. Words <=3 chars
# are already dropped by the length filter, so this lists only len>=4 stopwords.
_SCORING_STOPWORDS = {
    "this", "that", "these", "those", "with", "from", "into", "onto", "over",
    "under", "about", "above", "below", "between", "through", "throughout",
    "during", "before", "after", "while", "because", "since", "until", "upon",
    "within", "without", "against", "among", "across", "around", "behind",
    "beyond", "what", "when", "where", "which", "whom", "whose", "whether",
    "have", "having", "been", "being", "were", "will", "would", "could",
    "should", "shall", "might", "must", "does", "done", "doing", "their",
    "there", "them", "they", "then", "than", "your", "yours", "ours", "more",
    "most", "much", "many", "some", "such", "only", "also", "very", "just",
    "like", "here", "each", "both", "either", "neither", "every", "less",
    "least", "same", "other", "another", "able",
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
class _ScoringOutcome:
    """Internal: scores plus provenance of which scorer produced them.

    scorer_path is one of:
      - "heuristic_only"          no LLM client configured; heuristic is the primary scorer
      - "llm"                     LLM scorer returned exactly one score per source
      - "llm_fallback_heuristic"  LLM call/parse failed; degraded keyword heuristic used
    fallback_reason is populated only on the fallback path.
    """
    scores: list[float]
    scorer_path: str
    fallback_reason: Optional[str] = None


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
    # Scorer provenance (Q3 observability): which scorer produced source_scores,
    # and on the degraded fallback path, why. Lets the caller distinguish a
    # confident LLM-scored REJECT from one derived from the keyword heuristic.
    scorer_path: Optional[str] = None
    fallback_reason: Optional[str] = None
    # A1: True whenever the LLM relevance scorer failed and the degraded keyword
    # heuristic produced these scores (scorer_path == "llm_fallback_heuristic").
    # Surfaced so a synthesis that proceeded over a degraded gate carries a
    # caveat. Set on every decision branch, including the evidence-gated rescue.
    gate_degraded: bool = False
    # Q2: the normalized caller-supplied focus the gate scored relevance against
    # instead of the full query; None when no focus was applied. Echoed for
    # observability so a caller knows sources were judged against a narrowed
    # focus, not the full query. Set on every decision branch.
    gate_focus: Optional[str] = None


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

    # A2b retry directive: when the first scoring attempt yields no usable parse
    # (empty text or a count that does not map 1:1 to sources), retry once with
    # a strict machine-parseable format. Reasoning models otherwise emit prose
    # or chain-of-thought that _parse_scores cannot map to N scores.
    SCORING_RETRY_PROMPT = """Rate each source's relevance to the query from 0.0 to 1.0.

Query: {query}

Sources:
{sources}

Output ONLY a JSON array of exactly {n} floats in [0,1], one per source, in order.
No prose, no reasoning, no markdown, no code fence. Example for 3 sources: [0.8, 0.5, 0.2]"""

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
        gate_focus: Optional[str] = None,
    ) -> QualityGateResult:
        """
        Evaluate source quality for query.

        Args:
            query: The research query
            sources: Pre-gathered sources to evaluate
            gate_focus: Optional caller-supplied focus string (Q2). When set
                (non-empty after strip), source RELEVANCE is scored against the
                focus instead of the full query — a precision lever for verbose
                queries. Everything else (entity extraction, suggestions, the
                post-synthesis verifier) still uses the full query; entity-
                balanced promotion is skipped under an active focus. Omitted /
                None / whitespace → behaves exactly as before.

        Returns:
            QualityGateResult with decision and filtered sources
        """
        # Q2: relevance is scored against the focus when supplied; the full query
        # still drives entity extraction, suggestions, and output verification.
        focus_applied = bool(gate_focus and gate_focus.strip())
        scoring_target = gate_focus.strip() if focus_applied else query
        focus_echo = scoring_target if focus_applied else None

        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided. Try searching with broader terms.",
                reason="No sources to evaluate",
                scorer_path=None,
                fallback_reason="no_sources",
                gate_focus=focus_echo,
            )

        # Score each source for relevance (against the focus when one was
        # supplied, else the full query), recording scorer provenance.
        outcome = await self._score_sources(scoring_target, sources)
        scores = outcome.scores

        avg_quality = sum(scores) / len(scores)

        # A1: the synthesis-skip is only justified when the scorer is reliable.
        # On the degraded keyword-heuristic path (the LLM scorer failed) a low
        # average is weak evidence of irrelevance, so REJECT gets an
        # evidence-gated rescue below and every result is flagged gate_degraded.
        degraded = outcome.scorer_path == "llm_fallback_heuristic"

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
            # A1 evidence-gated rescue: on the degraded keyword-heuristic path,
            # retain any source the (de-diluted) heuristic still scores >=
            # pass_threshold rather than hard-REJECTing on a scorer we know
            # failed. If none clears pass, fail CLOSED. Scope: ONLY
            # llm_fallback_heuristic — a no-client heuristic_only run is a
            # legitimate primary scorer and a confident `llm` REJECT stands.
            if degraded:
                rescued = [s for s, sc in zip(sources, scores) if sc >= self.pass_threshold]
                if rescued:
                    rejected = [s for s, sc in zip(sources, scores) if sc < self.pass_threshold]
                    return QualityGateResult(
                        decision=QualityDecision.PARTIAL,
                        avg_quality=avg_quality,
                        good_sources=rescued,
                        rejected_sources=rejected,
                        source_scores=scores,
                        reason=(
                            f"relevance gate degraded (llm_fallback_heuristic); avg "
                            f"{avg_quality:.2f} below reject floor but retained "
                            f"{len(rescued)} source(s) clearing the pass threshold "
                            f"under the keyword heuristic"
                        ),
                        scorer_path=outcome.scorer_path,
                        fallback_reason=outcome.fallback_reason,
                        gate_degraded=True,
                        gate_focus=focus_echo,
                    )
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
                scorer_path=outcome.scorer_path,
                fallback_reason=outcome.fallback_reason,
                gate_degraded=degraded,
                gate_focus=focus_echo,
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
        #
        # Q2: skipped entirely under an active gate_focus. Promotion ranks by
        # FULL-QUERY entity centrality with no focus-relevance floor, so under a
        # focus it could resurrect a vendor-central but focus-irrelevant source
        # and defeat the caller's narrowing. The blackout it guards against is a
        # FULL-multi-entity-query dilution artifact that does not apply when the
        # scorer judges against a narrow focus (codex design 019e4683 T2).
        if self.entity_balanced and rejected_with_scores and not focus_applied:
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
                scorer_path=outcome.scorer_path,
                fallback_reason=outcome.fallback_reason,
                gate_degraded=degraded,
                gate_focus=focus_echo,
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
                scorer_path=outcome.scorer_path,
                fallback_reason=outcome.fallback_reason,
                gate_degraded=degraded,
                gate_focus=focus_echo,
            )

    async def _score_sources(
        self,
        query: str,
        sources: list,
    ) -> _ScoringOutcome:
        """Score sources, recording which scorer ran and why any fallback fired.

        Returns a _ScoringOutcome carrying the scores plus provenance so the
        caller can tell a confident LLM-scored decision apart from one derived
        from the degraded keyword heuristic (Q3 observability).

        Hardened scorer (A2): a reasoning-aware token budget so chain-of-thought
        does not starve the scores out of `content`, plus one strict-format
        retry before the keyword-heuristic fallback. At most two scoring calls.
        """
        if not self.llm_client:
            return _ScoringOutcome(
                self._score_sources_heuristic(query, sources),
                "heuristic_only",
            )

        expected = len(sources)
        formatted = self._format_sources(sources)
        # A2a: reasoning models spend output tokens on chain-of-thought before
        # the scores land in `content`; a flat 500 gets consumed by CoT and the
        # answer never lands (the silent-fallback root trigger). Give reasoning
        # models a modest scoring-specific headroom; non-reasoning models keep
        # the flat 500-token behavior (codex design 019e4569 T4-F2).
        headroom = settings.llm_scoring_headroom if is_reasoning_model(self.model) else 0
        budget = min(500 + headroom, settings.llm_max_tokens)

        try:
            scores, reason = await self._attempt_llm_scores(
                self.SCORING_PROMPT.format(query=query, sources=formatted),
                expected,
                budget,
            )
            # A2b: one retry with a strict machine-parseable directive when the
            # first attempt produced no usable parse (empty text or wrong count).
            if scores is None:
                scores, reason = await self._attempt_llm_scores(
                    self.SCORING_RETRY_PROMPT.format(
                        n=expected, query=query, sources=formatted
                    ),
                    expected,
                    budget,
                )
        except Exception as exc:
            # Network/transport error - fall back to the deterministic heuristic,
            # recording that the LLM scorer was attempted and failed.
            return _ScoringOutcome(
                self._score_sources_heuristic(query, sources),
                "llm_fallback_heuristic",
                f"llm_call_failed: {type(exc).__name__}",
            )

        if scores is None:
            # Both attempts failed to yield exactly `expected` parseable scores.
            # Fall back rather than synthesize over guessed scores; `reason`
            # carries the last attempt's failure (empty_response or
            # score_count_mismatch) for Q3 diagnostics.
            return _ScoringOutcome(
                self._score_sources_heuristic(query, sources),
                "llm_fallback_heuristic",
                reason,
            )

        return _ScoringOutcome(scores, "llm")

    async def _attempt_llm_scores(
        self,
        prompt: str,
        expected_count: int,
        budget: int,
    ) -> tuple[Optional[list[float]], Optional[str]]:
        """One scoring call + parse.

        Returns (scores, None) when the response parsed to exactly
        expected_count in-range scores; otherwise (None, reason) where reason is
        "empty_response" or "score_count_mismatch: ...". Transport exceptions
        propagate to the caller.
        """
        output = await self._call_llm(
            prompt, max_tokens=budget, mode=ExtractionMode.PARSE_REQUIRED
        )
        if not output.text:
            return None, "empty_response"
        scores = self._parse_scores(output.text, expected_count)
        if len(scores) != expected_count:
            return None, (
                f"score_count_mismatch: parsed {len(scores)} for "
                f"{expected_count} sources"
            )
        return scores, None

    def _score_sources_heuristic(
        self,
        query: str,
        sources: list,
    ) -> list[float]:
        """Score sources by query-term coverage (de-diluted - Q1).

        Length-independent: a saturating function of the number of DISTINCT
        content-bearing query terms a source matches at a TOKEN BOUNDARY, over
        title + content. The old `matches / len(query_terms)` drove every score
        toward zero as the query grew (the dilution bug), so a 38-term brief
        rejected on-topic sources a 4-term query passed. Reused on the degraded
        fallback path, so its scores must stay meaningful (codex 019e4569).

        - Stopwords + the >3-char filter drop function words so they don't enter
          the matched-term set (Q1a).
        - Matching is `\\b`-bounded via _entity_in_text so "research" does not
          match "researcher" (Q1b).
        - Title + content are both searched (Q1c).
        - score = 1 - exp(-0.25 * matched): length-independent, monotonic, and a
          single match (~0.22) is well below pass (Q1d, k=0.25).
        """
        query_terms = {
            word.lower()
            for word in re.findall(r'\b\w+\b', query)
            if len(word) > 3 and word.lower() not in _SCORING_STOPWORDS
        }

        if not query_terms:
            return [0.5] * len(sources)

        scores = []
        for source in sources:
            text_lower = f"{self._get_title(source)} {self._get_content(source)}".lower()
            matched = sum(
                1 for term in query_terms if _entity_in_text(text_lower, term)
            )
            scores.append(1.0 - math.exp(-0.25 * matched))

        return scores

    def _parse_scores(self, response: str, expected_count: int) -> list[float]:
        """Parse relevance scores from an LLM response.

        Two paths, tried in order:

        1. JSON array (only when expected_count >= 2): scan every bracketed
           candidate, accept the first that json-decodes to a list of EXACTLY
           expected_count numeric items where every RAW value is already in
           [0,1]. An out-of-range value disqualifies the whole candidate - a
           prose "[1, 2]" must not be clamped into [1.0, 1.0]. If none qualify,
           fall through. expected_count == 1 skips this path: a lone "[1]"
           source reference would be length-1-valid and misread as a 1.0 score.
        2. Per line: strip a leading enumerator / source label, then prefer a
           decimal in [0,1]; else the first bare number in [0,1]; else nothing.
           ("Source 1: 0.8" -> 0.8, never 1.0.)

        Returns exactly the scores it could parse - no padding, no clamping of
        out-of-range values. The caller compares the count against
        expected_count to decide whether to retry or fall back.
        """
        # Path 1: exact-length JSON array of natively in-range values.
        if expected_count >= 2:
            for candidate in re.findall(r"\[[^\[\]]*\]", response):
                try:
                    parsed = json.loads(candidate)
                except (ValueError, TypeError):
                    continue
                if not isinstance(parsed, list) or len(parsed) != expected_count:
                    continue
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in parsed):
                    continue
                if all(0.0 <= float(v) <= 1.0 for v in parsed):
                    return [float(v) for v in parsed]

        # Path 2: per line.
        scores: list[float] = []
        for raw_line in response.strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            # Strip a leading enumerator / source label ("1. ", "2) ",
            # "Source 3: "). Require trailing whitespace so a decimal score like
            # "0.85" is never mistaken for the enumerator "0." + "85".
            line = re.sub(
                r"^\s*(?:source\s*)?\d+\s*[:.)]\s+", "", line, flags=re.IGNORECASE
            )
            numbers = re.findall(r"\d+\.\d+|\d+", line)
            chosen: Optional[float] = None
            for tok in numbers:  # prefer a decimal in range
                if "." in tok and 0.0 <= float(tok) <= 1.0:
                    chosen = float(tok)
                    break
            if chosen is None:  # else the first bare number in range
                for tok in numbers:
                    if "." not in tok and 0.0 <= float(tok) <= 1.0:
                        chosen = float(tok)
                        break
            if chosen is not None:
                scores.append(chosen)

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
        gate_focus: Optional[str] = None,
    ) -> QualityGateResult:
        """
        Synchronous evaluation using heuristics only.

        Useful for quick evaluation without async overhead. `gate_focus` mirrors
        evaluate() (Q2): when supplied, relevance is scored against the focus
        instead of the full query; omitted/None/whitespace → full query.
        """
        focus_applied = bool(gate_focus and gate_focus.strip())
        scoring_target = gate_focus.strip() if focus_applied else query
        focus_echo = scoring_target if focus_applied else None

        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided.",
                reason="No sources to evaluate",
                scorer_path=None,
                fallback_reason="no_sources",
                gate_focus=focus_echo,
            )

        scores = self._score_sources_heuristic(scoring_target, sources)
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
                scorer_path="heuristic_only",
                gate_focus=focus_echo,
            )
        elif len(good_sources) < len(sources):
            return QualityGateResult(
                decision=QualityDecision.PARTIAL,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=rejected_sources,
                source_scores=scores,
                scorer_path="heuristic_only",
                gate_focus=focus_echo,
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
                scorer_path="heuristic_only",
                gate_focus=focus_echo,
            )
