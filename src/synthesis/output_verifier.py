"""Post-synthesis output verification.

The synthesize path can return output that superficially looks fine but is
not usable: an empty completion, a chain-of-thought trace returned in place
of an answer, a generation truncated by the token limit, a multi-section
synthesis with a failed contributing sub-call, or an answer with no citations
despite having sources. The pre-synthesis source quality gate does not catch
any of these - it scores input relevance, not output.

This module defines the verdict type and the shared post-synthesis verifier
used by both the MCP synthesize tool and the REST synthesis routes.
"""

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..llm_utils import LLMOutput
from .citations import (
    detect_legacy_markers,
    detect_mixed_markers,
    has_numeric_citation_marker,
)
from .contradictions import ContradictionDetectionResult
from .quality_gate import _entity_in_text
from .sentence_utils import split_sentences


# Forward-compat verdict envelope (Phase 0 scaffolding). The existing
# `hard_failures` / `soft_warnings` / `.passed` shape is preserved verbatim
# below; these new fields are populated by future phases:
# - Phase 1: `failure_codes` (gap_unscoped, gap_section_polluted,
#   gap_declared_but_section_open, gap_group_heading_unsupported); also
#   `verdict_class == "calibrated_gap"` when the structural gap parser
#   accepts a declared gap.
# - Phase 5a: `warnings[code=coverage_grid_uncited_uncovered_cells]` etc.
# - Phase 5b: `warnings[code=uncovered_cell_unacknowledged]` (after a 14-day
#   fixture green-light) and the matching hard_failures entry.
# - Phase 6: `retry_advice` populated when the verifier can recommend a
#   surface-aware retry (gather_more_sources / resynthesize_same_sources /
#   abort) and `diagnostics.tier_composition`.
#
# Phase 0 only sets `verdict_class` automatically (= "hard_fail" if
# hard_failures else "pass"). Everything else defaults so a Phase 0 caller
# sees behavior identical to pre-envelope output.
VerdictClass = Literal["pass", "calibrated_gap", "hard_fail"]


@dataclass
class VerdictWarning:
    """A structured advisory warning, mirroring a `soft_warnings` string entry.

    The verifier emits both shapes in parallel: `soft_warnings` (list of
    strings) remains the existing human-readable channel; `warnings` (list of
    `VerdictWarning`) is the machine-readable parallel that downstream
    consumers (Phase 6 retry advice, Phase 7 evolution loop) can pattern-match
    on without re-parsing prose. Phase 0 emits empty.
    """
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"


@dataclass
class VerdictDiagnostics:
    """Structured diagnostics produced by the verifier.

    Field-granular dict slots so future phases can populate them independently
    without bumping a schema version. Phase 0 leaves all slots None / empty.
    """
    gate_diagnostics: Optional[dict] = None
    tier_composition: Optional[dict] = None
    gap_declarations: list[str] = field(default_factory=list)
    contracrow_result: Optional[dict] = None
    coverage_grid_summary: Optional[dict] = None
    bm25_mismatch_info: Optional[dict] = None


@dataclass
class RetryAdvice:
    """Surface-aware retry advice emitted on hard-failure (Phase 6 territory).

    Phase 0 always emits None. Phase 6 populates this when the verifier sees
    a hard fail that the caller can address by re-discovery, re-synthesis, or
    abort. Pure-synthesis surfaces never re-discover internally — the advice
    is emitted FOR the caller (an orchestrator or research-workflow).
    """
    caller_action: Literal["gather_more_sources", "resynthesize_same_sources", "abort"]
    missing_entities: list[str] = field(default_factory=list)
    missing_aspects: list[tuple[str, str]] = field(default_factory=list)
    suggested_queries: list[str] = field(default_factory=list)
    rationale: str = ""


# Explicit gap-framing phrases that indicate the synthesis acknowledged a
# missing source rather than hallucinating about an uncovered entity. The
# check is SENTENCE-LEVEL: the gap-framing phrase and the entity must appear
# in the same sentence (delimited by `.`, `!`, `?`). Earlier window-based
# check was too permissive — "no source available for LinkUp" leaked across
# the sentence boundary to frame a later "Serper" mention.
_GAP_FRAMING_PHRASES = (
    "no source", "without source", "no citation", "uncited",
    "not in the gathered", "not in our gathered", "not in any gathered",
    "not covered by", "not covered in", "not documented",
    "no data on", "no information on", "no information about",
    "not available", "no available", "could not find", "couldn't find",
    "no documentation", "missing from", "absent from",
    "gap in", "coverage gap", "not present in",
)


@dataclass
class SynthesisVerdict:
    """Result of verifying synthesis output before it is returned to a caller.

    `hard_failures` are blocking - the output must not be cached or relayed as
    a successful synthesis. `soft_warnings` are advisory - the output is usable
    but should be annotated for the caller.

    The remaining fields (`verdict_class`, `failure_codes`, `warnings`,
    `diagnostics`, `retry_advice`) are forward-compat envelope scaffolding
    populated by later phases (1, 5a, 5b, 6). Phase 0 sets `verdict_class`
    automatically (= "hard_fail" if any hard_failures else "pass") and leaves
    the rest at their defaults so existing callers see identical observable
    behavior. The `passed` property's semantics are unchanged.
    """
    hard_failures: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    verdict_class: VerdictClass = "pass"
    failure_codes: list[str] = field(default_factory=list)
    warnings: list[VerdictWarning] = field(default_factory=list)
    diagnostics: VerdictDiagnostics = field(default_factory=VerdictDiagnostics)
    retry_advice: Optional[RetryAdvice] = None

    def __post_init__(self) -> None:
        """Reconcile `verdict_class` with `hard_failures` shape on construction.

        Without this, a direct constructor `SynthesisVerdict(hard_failures=["x"])`
        would have `passed=False` but `verdict_class="pass"` (the default), which
        is a contradictory state Phase 1/5/6 callers could easily produce. The
        rule is:
        - hard_failures non-empty → verdict_class = "hard_fail" (overrides any
          other value; a hard failure dominates).
        - hard_failures empty AND verdict_class == "hard_fail" → demote to
          "pass" (verdict_class lied about an empty hard_failures list).
        - hard_failures empty AND verdict_class == "calibrated_gap" → preserve
          the calibrated_gap signal (Phase 1's structural-gap acknowledgement).
        - hard_failures empty AND verdict_class == "pass" → no change.

        Codex Turn 1 F5 (consistency on direct construction).
        """
        if self.hard_failures:
            self.verdict_class = "hard_fail"
        elif self.verdict_class == "hard_fail":
            self.verdict_class = "pass"
        # "pass" and "calibrated_gap" with empty hard_failures: preserve.

    @property
    def passed(self) -> bool:
        """True when there are no blocking failures (output is safe to cache and relay)."""
        return not self.hard_failures


def _entity_acknowledges_gap(content_lower: str, entity_lower: str) -> bool:
    """True if some sentence frames THIS entity as a source gap.

    Sentence-level: the entity must appear in at least one sentence that also
    contains a gap-framing phrase. Boundary-safe entity match (Turn 3 codex
    T3F1): otherwise a sentence mentioning "example" would falsely frame an
    "Exa" entity that's never actually discussed in it.

    Sentence-level vs window-level (Turn 2 fix-iteration): an earlier
    window-based check (40 chars before/after entity) leaked framing across
    sentence boundaries — "no source available for LinkUp. Serper costs..."
    incorrectly framed Serper. Splitting at sentence delimiters scopes the
    framing to the entity it actually qualifies.
    """
    for sentence in split_sentences(content_lower):
        if _entity_in_text(sentence, entity_lower) and any(
            phrase in sentence for phrase in _GAP_FRAMING_PHRASES
        ):
            return True
    return False


def _output_acknowledges_gap(content_lower: str, uncovered_entities: list[str]) -> bool:
    """True if the synthesis explicitly frames EVERY uncovered entity as a gap.

    Thin all-or-nothing wrapper over `_entity_acknowledges_gap` (codex T2). The
    verifier now classifies framing PER ENTITY (a framed entity is soft-warned
    even when others are un-framed); this aggregate predicate is retained for
    callers that want the original "all framed" semantics.
    """
    return all(
        _entity_acknowledges_gap(content_lower, e.lower()) for e in uncovered_entities
    )


def _entity_has_adjacent_citation(content_lower: str, entity_lower: str) -> bool:
    """True if some sentence mentions the entity AND carries a `[N]` citation.

    Sentence-level, mirroring `_output_acknowledges_gap`: the numeric citation
    marker must co-occur with the entity in the same sentence (delimited by
    `.`, `!`, `?`) for the synthesis to count as binding a source to that
    entity. Used to split a discussed-but-uncovered entity into cited (the
    stronger "treat as unverified" soft caveat - the fabricated-attribution
    shape) vs uncited (query-framing / coined-label vocabulary -> the lighter
    "verify these labels" note). BOTH are soft - entity-coverage no longer
    hard-fails (codex DESIGN 019e5b0f); the split only graduates caveat strength.
    ISS-20260604-001.
    """
    for sentence in split_sentences(content_lower):
        if _entity_in_text(sentence, entity_lower) and has_numeric_citation_marker(sentence):
            return True
    return False


# Common English words that appear ALL-CAPS in a query only as shouted emphasis,
# never as an acronym ENTITY or standard. _is_uppercase_framing_token softens an
# uncovered query token (single OR multi-word/hyphenated) only when EVERY part is
# in this set, so "MEASUREMENT PLANE" / "CONTROL PLANE" / "NET-NEW" / "NET" / "NEW"
# read as framing while an acronym standard whose parts are NOT common words
# ("PCI DSS", "SOC 2", "EU AI ACT", "ISO 27001", "HIPAA BAA") falls through to
# the stronger "treat as unverified" caveat instead of the "emphasis/framing"
# note - a numeric or acronym part is never in this set (codex T3 M2 / T4 M1).
# Both are SOFT now (entity-coverage no longer hard-fails, codex DESIGN
# 019e5b0f); this set only routes warning TEXT. Curated and extensible, mirroring the BAA/SOC
# compliance-acronym stopwords in quality_gate.py; deliberately EXCLUDES words
# that collide with real all-caps tech entities (REST, CORE, BASE, EDGE, GO).
# Lowercased membership.
_ALLCAPS_COMMON_WORDS: frozenset[str] = frozenset({
    # Quantifiers / determiners / generic modifiers shouted for emphasis.
    "net", "new", "old", "all", "none", "any", "both", "each",
    "more", "less", "most", "main", "full", "part", "real", "true",
    "false", "same", "next", "last", "first", "final", "total",
    "single", "multi", "multiple", "joint", "shared", "common",
    "generic", "default", "custom", "raw", "overall", "general",
    # Structural / architecture framing nouns (".. PLANE / LAYER / STAGE ..").
    "plane", "layer", "level", "tier", "stage", "phase", "step",
    "scope", "scale", "range", "lane", "spine", "set", "group",
    "mode", "state", "view", "flow", "loop", "node", "unit",
    "block", "field", "frame",
    # Domain framing nouns the operator uses in plane / control-plane queries.
    "measurement", "control", "composition", "application",
    "knowledge", "execution", "evidence", "management",
    "forwarding", "user", "data", "work", "item", "run", "plan",
    "design", "review", "build", "gate", "receipt", "charter",
})


def _is_uppercase_framing_token(entity: str) -> bool:
    """True if a fully-uppercase query token is shouted emphasis/framing rather
    than an acronym ENTITY or standard. Entity-coverage is all-soft now (codex
    DESIGN 019e5b0f), so this only graduates the warning TEXT: a framing token
    gets the "emphasis/framing" note, a real acronym/standard gets the stronger
    "treat as unverified" caveat. Nothing here hard-fails.

    A token is treated as framing ONLY when every space/hyphen-delimited part is
    a common English word in _ALLCAPS_COMMON_WORDS:
    - "MEASUREMENT PLANE" / "CONTROL PLANE" / "NET-NEW" / "NET" / "NEW" -> all
      parts are common words -> framing -> the "emphasis/framing" note.
    - "AWS" / "GCP" / "HIPAA" / "BAA" (single acronym) and "PCI DSS" / "SOC 2" /
      "EU AI ACT" / "ISO 27001" / "HIPAA BAA" (multi-token acronym STANDARDS) ->
      at least one part is an acronym or number, never in the set -> NOT framing
      -> the stronger "treat as unverified" caveat (codex T3 M2 / T4 M1).
    A real multi-word entity is Title-case ("Review Board"), so the ALL-CAPS gate
    (entity.isupper()) already excludes it before the part check.
    """
    if not entity.isupper():
        return False
    parts = entity.lower().replace("-", " ").split()
    return bool(parts) and all(p in _ALLCAPS_COMMON_WORDS for p in parts)


# Known production aliases for query entities whose exact phrase is commonly
# absent from sources that nonetheless cover the SAME concept under a different
# surface form. Lowercased keys; each value is the set of source surface forms
# that count as coverage. Curated and intentionally SMALL (codex DESIGN
# 019e5b0f): an alias is a real, deterministic production variant of the same
# entity, NEVER a broad ecosystem sibling. "docker engine"/"dockerd" is the case
# the head-token proxy got wrong - "docker" appearing is not evidence the
# "Engine" claim is grounded, but "dockerd" IS the same daemon under another name.
_COVERAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "docker engine": ("dockerd", "docker daemon"),
    "wsl": ("wsl2", "wsl 2", "windows subsystem for linux"),
    "postgres": ("postgresql",),
}

# Single-token version/suffix coverage: minimum stem length so a short ambiguous
# token cannot collapse onto an unrelated identifier. >= 4 chars normally, or
# >= 3 when the entity is ALL-CAPS (an explicit acronym like WSL/GPT). Admits
# WSL/GPT/Postgres; rejects "V"->"v2", "AI"->"ai2", Title-case "Net"->"net8".
_MIN_VERSION_STEM = 4
_MIN_VERSION_STEM_ALLCAPS = 3


def _known_alias_covered(entity_lower: str, sources_text: str) -> bool:
    """True if a curated production alias of the entity appears in the sources.

    The alias map is the correct fix for an entity the sources express under a
    different name (codex DESIGN 019e5b0f: head-token coverage alone conflates
    "the ecosystem is mentioned" with "this cited claim is grounded").
    """
    aliases = _COVERAGE_ALIASES.get(entity_lower)
    if not aliases:
        return False
    return any(_entity_in_text(sources_text, alias) for alias in aliases)


def _version_suffix_covered(entity: str, sources_text: str) -> bool:
    """True if a single-token entity is covered by a version/suffix surface form
    in the sources ("WSL" -> "wsl2"/"wsl 2"/"wsl-2", "Postgres" -> "postgres17").

    Direction is entity-without-version -> source-with-version (the reported
    "WSL" vs "wsl2" shape). The reverse (entity carries the version, source does
    not) is a separate, unreported case and is intentionally out of scope. A
    purely-alphabetic version tail ("gpt-4o") is also out of scope - the rule
    matches a numeric release only (codex DESIGN 019e5b0f).
    """
    stem = entity.lower()
    # Single token only: a space/hyphen means a phrase or a standard ("SOC 2"),
    # handled by the alias map or kept hard, never by the version rule.
    if " " in stem or "-" in stem:
        return False
    # The stem must carry NO version of its own: an entity that already contains
    # a digit ("WSL2", "ISO27001") is the reverse direction (entity-with-version)
    # and must NOT become a new stem that softens on a further glued numeric
    # suffix in the sources ("WSL2" vs "wsl22", "ISO27001" vs "iso270012022") -
    # exact coverage fails on the token boundary, and without this guard the
    # version regex would re-open the exact false-positive class this rule exists
    # to close (codex 019e5b0f Low).
    if any(ch.isdigit() for ch in stem):
        return False
    min_len = _MIN_VERSION_STEM_ALLCAPS if entity.isupper() else _MIN_VERSION_STEM
    if len(stem) < min_len:
        return False
    # stem, optional single space/hyphen, then a numeric release, whole-token.
    return re.search(r"\b" + re.escape(stem) + r"[ \-]?\d+\b", sources_text) is not None


def _is_surface_variant_covered(entity: str, sources_text: str) -> bool:
    """True if an uncovered entity's exact-phrase miss is explained by a known
    production alias or a single-token version/suffix form in the sources - a
    lexical variant of a COVERED concept, not a fabricated attribution. Narrow by
    design (codex DESIGN 019e5b0f): NOT mere head-token presence."""
    return _known_alias_covered(entity.lower(), sources_text) or _version_suffix_covered(
        entity, sources_text
    )


def verify_synthesis_output(
    content: str,
    llm_output: Optional[LLMOutput],
    cited_count: int,
    source_count: int,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    *,
    query_entities: Optional[list[str]] = None,
    sources_text: Optional[str] = None,
) -> SynthesisVerdict:
    """Verify a completed synthesis before it is cached or relayed to a caller.

    Hard failures mean the output is not a usable synthesis and must not be
    presented as a successful one (and must not be cached). Soft warnings mean
    the output is usable but should be annotated for the caller.

    Args:
        content: the final synthesis text.
        llm_output: the provenance/truncation signal carried from the synthesis
            call(s), or None if unavailable.
        cited_count: number of distinct sources cited in `content`.
        source_count: number of sources provided to the synthesis.
        contradiction_result: the contradiction-detection result, if the path
            ran contradiction detection.
        query_entities: optional list of capitalized entities extracted from
            the query (vendor / product / library names). When provided
            alongside `sources_text`, the verifier checks that every entity
            the synthesis discusses is grounded in at least one retained
            source. Catches the hallucination class where the relevance gate
            filters out all sources for an entity but the LLM writes about
            it anyway from prior knowledge.
        sources_text: optional concatenated lowercase text (title + content)
            of all retained sources, used for the entity-coverage check.
            Pre-lowercased to avoid per-call cost.
    """
    verdict = SynthesisVerdict()
    has_content = bool(content and content.strip())

    # --- hard gates: the output is not a usable synthesis ---
    if not has_content:
        verdict.hard_failures.append("synthesis produced no answer content")
    elif llm_output is not None and llm_output.reasoning_only:
        verdict.hard_failures.append(
            "synthesis returned a reasoning trace instead of an answer"
        )

    if llm_output is not None and llm_output.truncated:
        verdict.hard_failures.append(
            "synthesis was truncated by the token limit (finish_reason=length) "
            "even after the retry at the ceiling"
        )

    if llm_output is not None and llm_output.subcall_failed:
        verdict.hard_failures.append(
            "a contributing synthesis sub-call produced no usable answer "
            "(empty, reasoning-only, or truncated) - the assembled synthesis "
            "is incomplete"
        )

    if has_content and source_count > 0 and cited_count == 0:
        verdict.hard_failures.append(
            f"synthesis cites none of the {source_count} provided sources"
        )

    # Entity-coverage check (ADVISORY). An entity discussed in the synthesis but
    # absent from every retained source MAY be hallucinated from prior model
    # knowledge (the gate filtered the source(s) covering it, and the model
    # responded anyway) - but it is just as often a legitimate gap: a specific
    # entity the authoritative sources never name, a lexical/alias variant, or
    # shouted query framing. So entity-coverage is SOFT throughout (codex DESIGN
    # 019e5b0f): a discussed-but-uncovered entity produces a soft caveat, never a
    # hard fail. `synthesize` compresses sources and ships to a downstream LLM; a
    # false-positive hard fail would destroy 100% of that compression, while a
    # real fabrication is served better by a caveat the consumer can discount
    # (June-2026 grounding literature: grounding is a per-claim advisory signal,
    # not an answer-level pass/fail). The branches below only graduate the caveat
    # TEXT - explicit gap-framing ("no source available for X") -> "frames the
    # gap"; all-caps framing -> "emphasis/framing"; known alias / version variant
    # -> "surface-form variant"; everything else, cited-adjacent -> the strong
    # "treat as UNVERIFIED" note. Structural gates (empty / reasoning-only /
    # truncated / subcall-failed / zero-citations) are the only HARD gates.
    if has_content and query_entities and sources_text:
        content_lower = content.lower()
        # Only consider entities the synthesis actually discusses.
        # Turn 3 codex T3F1: boundary-safe matching — substring `in` would
        # let "Exa" match "example" so the synthesis appears to discuss
        # entities it doesn't, and an "Exa" entity appears "covered" by
        # an unrelated source whose body contains "example".
        discussed = [e for e in query_entities if _entity_in_text(content_lower, e.lower())]
        if discussed:
            uncovered = [e for e in discussed if not _entity_in_text(sources_text, e.lower())]
            if uncovered:
                # Per-entity gap framing FIRST (codex T2). An entity the
                # synthesis explicitly frames as a source gap ("no source for
                # X") is a grounded acknowledgement, not a hallucination - even
                # when its framing sentence happens to carry a citation marker.
                # Classifying framing per entity (not all-or-nothing) keeps a
                # framed entity OUT of the citation-adjacency check below, which
                # would otherwise route it to the stronger "treat as unverified"
                # caveat (on the marker in its own gap sentence) instead of the
                # gentler "frames the gap" note - all soft now (codex DESIGN
                # 019e5b0f), but the per-entity ordering still picks the caveat.
                framed_uncovered = [
                    e for e in uncovered
                    if _entity_acknowledges_gap(content_lower, e.lower())
                ]
                remaining = [e for e in uncovered if e not in framed_uncovered]
                if framed_uncovered:
                    verdict.soft_warnings.append(
                        f"synthesis discusses {framed_uncovered} without source "
                        "evidence but explicitly frames the gap - operator "
                        "should still verify the framing is accurate"
                    )
                if remaining:
                    # Shouted uppercase query FRAMING ("MEASUREMENT PLANE", "NET-NEW",
                    # "NET", "NEW") is emphasis, not an entity the synthesis fabricates
                    # a source for; its citation-adjacency is coincidental (the framing
                    # word shares a sentence with a real cited claim), so it must NOT
                    # hard-fail (nothing in entity-coverage hard-fails anymore - codex
                    # DESIGN 019e5b0f); the carve-out now only graduates warning TEXT,
                    # routing pure framing ("MEASUREMENT PLANE") to an "emphasis/framing"
                    # note while a genuine acronym (AWS / GCP / HIPAA / BAA) or acronym
                    # STANDARD ("PCI DSS", "SOC 2", "ISO 27001") falls through to the
                    # stronger "treat as unverified" entity-coverage caveat - so the
                    # partition uses _is_uppercase_framing_token (matches only when EVERY
                    # part is a curated common word), NOT a blanket isupper().
                    # Title-case descriptive adjectives ("Open-source") are a SEPARATE,
                    # non-isupper() sub-class out of scope here.
                    emphasis_uncovered = [e for e in remaining if _is_uppercase_framing_token(e)]
                    substantive = [e for e in remaining if e not in emphasis_uncovered]
                    if emphasis_uncovered:
                        verdict.soft_warnings.append(
                            f"synthesis uses uppercase query-framing tokens "
                            f"{emphasis_uncovered} that no retained source covers - "
                            "treated as emphasis/framing, not source-bound claims; "
                            "verify they are not ungrounded factual entities"
                        )
                    # Surface-form coverage downgrade (codex DESIGN 019e5b0f). An
                    # uncovered entity whose exact phrase is absent ONLY because the
                    # sources express it as a deterministic surface variant - a curated
                    # production alias ("Docker Engine" -> "dockerd"/"docker daemon") or
                    # a version/suffix form of a single token ("WSL" -> "wsl2") - is a
                    # lexical variant of a COVERED concept, not a fabricated attribution,
                    # so it gets the gentler "surface-form variant" note instead of the
                    # stronger "treat as unverified" caveat (all soft now, codex DESIGN
                    # 019e5b0f - this only routes warning TEXT). The bar is deliberately
                    # NARROW (alias OR version
                    # suffix), NOT mere head-token presence: "Docker Engine" softens
                    # because "dockerd" is an alias, NOT because "docker" appears (codex
                    # rejected head-token coverage - it conflates "ecosystem mentioned"
                    # with "this cited claim grounded"). A genuine fabrication ("Prisma
                    # is SSPL [3]" with no "prisma" anywhere) and acronym STANDARDS ("PCI
                    # DSS", "SOC 2", "ISO 27001", "EU AI ACT", "HIPAA BAA") have neither
                    # an alias nor a version variant, so they fall through to the generic
                    # entity-coverage caveat (the stronger "treat as unverified" note) -
                    # all soft now (codex DESIGN 019e5b0f); this carve-out only swaps in
                    # the more precise "surface-form variant present" wording for the
                    # cases it matches. Variant coverage of the entity is not the cited
                    # claim being grounded.
                    surface_variant = [
                        e for e in substantive
                        if _is_surface_variant_covered(e, sources_text)
                    ]
                    if surface_variant:
                        substantive = [e for e in substantive if e not in surface_variant]
                        verdict.soft_warnings.append(
                            f"synthesis discusses {surface_variant} which no retained "
                            "source covers by exact phrase, but a surface-form variant "
                            "(known alias or version suffix) IS present - treated as a "
                            "lexical variant of a covered concept, not a fabricated "
                            "attribution; verify the specific cited claim is grounded"
                        )
                    # Citation-adjacency split (ISS-20260604-001) over the UN-framed,
                    # non-emphasis remainder. Both halves are now ADVISORY (codex
                    # DESIGN 019e5b0f demoted entity-coverage from a hard gate to a
                    # soft warning); the split only graduates caveat STRENGTH:
                    #   - cited-adjacent (a `[N]` marker shares a sentence with the
                    #     uncovered entity) -> the strong "treat as unverified" caveat,
                    #     the old "Prisma is SSPL [3]" fabrication shape;
                    #   - uncited -> the lighter "verify these labels" note.
                    # NEITHER hard-fails. `synthesize` compresses sources and ships to a
                    # downstream LLM; "absent query entity + adjacent citation" is the
                    # same overloaded signal that produced three false-positive classes
                    # (all-caps framing, surface variants, legitimately-absent specific
                    # entities), and there is NO discriminator that catches a genuine
                    # fabrication without re-capturing those FPs (codex). So a false
                    # positive must cost only a spurious caveat, never the whole
                    # synthesis - the downstream LLM adjudicates the caveat (June-2026
                    # grounding literature concurs: grounding is a per-claim advisory
                    # signal, not an answer-level pass/fail). Structural gates (empty /
                    # reasoning-only / truncated / subcall-failed / zero-citations) stay
                    # hard. Mixed sentences are intentionally NOT exempted: "AssemblyAI
                    # and SufiSR both support X [1]" with SufiSR uncovered keeps the
                    # stronger cited caveat - the citation is not re-attributed to the
                    # covered co-entity.
                    cited_uncovered = [
                        e for e in substantive
                        if _entity_has_adjacent_citation(content_lower, e.lower())
                    ]
                    uncited_uncovered = [
                        e for e in substantive if e not in cited_uncovered
                    ]
                    if cited_uncovered:
                        verdict.soft_warnings.append(
                            f"synthesis binds source citations to query entities "
                            f"{cited_uncovered} not found verbatim in any retained "
                            "source - treat those cited claims as UNVERIFIED unless the "
                            "source text supports them via an alias, a surface variant, "
                            "or broader context; gather entity-specific sources to confirm"
                        )
                    if uncited_uncovered:
                        verdict.soft_warnings.append(
                            f"synthesis discusses {uncited_uncovered} with no "
                            "in-sentence citation and no retained source covers "
                            "them - verify these are query-framing labels and "
                            "not ungrounded claims; gather more sources if they "
                            "should be source-grounded"
                        )

    # --- soft annotations: usable, but flag for the caller ---
    if has_content and source_count > 0 and 0 < cited_count < source_count:
        verdict.soft_warnings.append(
            f"partial citation coverage: {cited_count} of {source_count} sources cited"
        )

    # Citation marker drift (v0.3.0, codex DESIGN session 019e39f7 Q7).
    # v0.3.0 unified every synthesis surface onto `[N]`. If the LLM still
    # emits legacy `[xx_<hex>]` markers the prompt has regressed (or the
    # model ignored the contract under deep-synthesis pressure). Surface
    # the drift as a soft warning so operators see a concrete diagnostic
    # rather than the generic "cites none" hard-fail message — especially
    # useful during the v0.3.0 migration window when prompt + extractor
    # changes are still bedding in. Hard-fail at cited_count==0 above still
    # fires for legacy-only output (because numeric extraction returns 0);
    # this warning is the diagnostic that explains WHY it fired.
    if has_content:
        legacy_markers = detect_legacy_markers(content)
        if legacy_markers:
            preview = ", ".join(legacy_markers[:3])
            more = f" (+{len(legacy_markers) - 3} more)" if len(legacy_markers) > 3 else ""
            if detect_mixed_markers(content):
                verdict.soft_warnings.append(
                    f"citation marker drift: synthesis emitted both `[N]` and "
                    f"legacy `[xx_<hex>]` markers — {len(legacy_markers)} legacy "
                    f"marker(s) ignored by numeric extractor: {preview}{more}"
                )
            else:
                verdict.soft_warnings.append(
                    f"citation marker drift: synthesis emitted only legacy "
                    f"`[xx_<hex>]` markers (no `[N]`) — prompt regression or "
                    f"model ignored the v0.3.0 contract. Markers: "
                    f"{preview}{more}"
                )

    if contradiction_result is not None:
        # C7/D4: a detector transport failure (fallback_used=True + error set) or a
        # heuristic fallback otherwise vanished here - the verifier only warned on
        # parse_failed. Surface both so "no contradictions" is never silently the
        # product of a failed/degraded detector run.
        if contradiction_result.error:
            _err = (contradiction_result.error or "")[:120]
            verdict.soft_warnings.append(
                f"contradiction detection failed and fell back to a heuristic "
                f"({_err}) - contradictions may exist but were not reliably checked"
            )
        elif contradiction_result.fallback_used:
            verdict.soft_warnings.append(
                "contradiction detection used the degraded heuristic detector "
                "- contradictions may exist but were not reliably checked"
            )
        elif contradiction_result.parse_failed:
            verdict.soft_warnings.append(
                "contradiction detection could not be parsed - contradictions "
                "may exist but were not surfaced"
            )
        elif contradiction_result.surfaced:
            verdict.soft_warnings.append(
                f"{len(contradiction_result.surfaced)} contradiction(s) detected "
                "- verify the synthesis surfaces them"
            )

    # Phase 0 envelope: derive `verdict_class` from the hard_failures shape.
    # "calibrated_gap" is reserved for Phase 1 (structural entity-section
    # parser); Phase 0 only distinguishes pass vs hard_fail, mirroring the
    # existing `.passed` property's semantics.
    verdict.verdict_class = "hard_fail" if verdict.hard_failures else "pass"

    return verdict


def annotate_with_verdict(output: str, verdict: SynthesisVerdict) -> str:
    """Annotate a synthesis output string with its verification verdict.

    Soft warnings are appended as advisory notes. A hard-gate failure prepends a
    clear failure header so the output is never relayed as a clean success.
    """
    result = output
    if verdict.soft_warnings:
        result = (
            result
            + "\n\n---\n*Verification notes: "
            + "; ".join(verdict.soft_warnings)
            + "*"
        )
    if not verdict.passed:
        result = (
            "# Synthesis verification FAILED\n\n"
            "This output is not a reliable synthesis:\n"
            + "\n".join(f"- {f}" for f in verdict.hard_failures)
            + "\n\n---\n(unverified output below, for debugging)\n\n"
            + result
        )
    return result
