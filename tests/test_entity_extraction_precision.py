"""Entity-extraction precision regression tests.

Bug (surfaced via a real research run, 2026-05-22): ``extract_query_entities``
over-extracted generic terms. Shape 3 matched any lowercase hyphenated English
compound (``opt-out`` / ``real-time`` / ``pre-recorded``) despite its docstring
claiming "caps or digits only", and Shape 1 grabbed capitalized common words
(``English`` / ``Need``) and compliance acronyms (``BAA``). Each pseudo-entity
that was missing from the retained source set hard-failed an otherwise-good
synthesis via the post-synthesis entity-coverage check.

Fix (codex DESIGN session 019e5031: T1 lock + T2 trailing-"." correction):
- Shape 3 gated by ``_is_hyphenated_entity`` = cap-or-digit OR membership in
  ``LOWERCASE_HYPHENATED_TOOL_ALLOWLIST``. Negative-lookaround pattern: trailing
  "." ALLOWED (so sentence-final ``Nova-3.`` matches) but trailing "-" excluded
  (so a 4+-hyphen chain can't partial-match its leading 3-hyphen prefix).
- Shape 1 stopwords extended with sentence verbs + language names + compliance
  acronyms.
- Verifier policy UNCHANGED (Q3 confidence-tiering deferred): every entity that
  IS extracted stays hard-fail eligible for the coverage check.

Precision pass 2 (ISS-20260606-001, codex session 019e721b T6): descriptive
query vocabulary was still over-extracted — evaluative adjectives (``Optimal``)
via Shape 1 and acronym-prefixed compounds (``AI-served``) via Shape 3. Both
pervade a cited synthesis and trip the v0.3.8 citation-adjacency split as phantom
"fabricated attributions". Fix is purely EXTRACTION (verdict logic unchanged):
evaluative adjectives are filtered ONLY as STANDALONE Shape-1 tokens (so a
multi-word "Scalable Capital" / "Optimal Dynamics" survives whole — codex T6 M1),
and the acronym-compound guard gates on a CURATED descriptive-tail set (so real
identifiers "AR-Foundation" / "gRPC-Web" survive — codex T6 M2).

These tests assert CORRECT behavior. A failure means the source regressed — not
that the test should be relaxed.
"""

import asyncio
from unittest.mock import MagicMock

from src.synthesis import (
    PreGatheredSource,
    extract_query_entities,
    verify_synthesis_output,
)
from src.synthesis.entity_allowlist import (
    CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST,
    LOWERCASE_HYPHENATED_TOOL_ALLOWLIST,
    LOWERCASE_TOOL_ALLOWLIST,
)
from src.synthesis.quality_gate import (
    QualityDecision,
    SourceQualityGate,
    _is_hyphenated_entity,
)


# The exact privacy-sensitive STT comparison query that produced the verifier
# false-positive hard-fail on the "entity" opt-out.
_REPORTED_QUERY = (
    "Compare cloud speech-to-text providers for batch English sales calls: "
    "AssemblyAI vs Deepgram Nova-3. Need diarization, opt-out of data "
    "retention, BAA, SOC2, real-time vs pre-recorded, two-party consent, "
    "cost-effective."
)


def _make_source(title: str, content: str) -> PreGatheredSource:
    return PreGatheredSource(
        origin="test",
        url=f"http://example.com/{title}",
        title=title,
        content=content,
        source_type="article",
    )


def _verify(content: str, entities: list[str], sources_text: str):
    """Run the post-synthesis verifier with the entity-coverage path enabled.

    llm_output=None and cited_count==source_count==1 isolate the
    entity-coverage check from the other hard gates.
    """
    return verify_synthesis_output(
        content,
        None,
        cited_count=1,
        source_count=1,
        query_entities=entities,
        sources_text=sources_text,
    )


# ============================================================
# Headline: the reported query yields ONLY the real entities
# ============================================================

def test_reported_query_yields_only_real_entities():
    # "Need" opens a sentence after the "Nova-3." period, so the sentence-
    # initial verb strip removes it; English (language) + BAA/SOC2 (acronyms) +
    # the Shape 3 lowercase compounds are all dropped. Only real entities remain.
    assert extract_query_entities(_REPORTED_QUERY) == [
        "AssemblyAI",
        "Deepgram Nova",
        "Nova-3",
    ]


# ============================================================
# False positives eliminated
# ============================================================

def test_generic_hyphenated_compounds_not_extracted():
    result = extract_query_entities(_REPORTED_QUERY)
    for junk in (
        "speech-to-text",
        "opt-out",
        "real-time",
        "pre-recorded",
        "two-party",
        "cost-effective",
    ):
        assert junk not in result


def test_language_name_not_extracted():
    # "English" is an always-stopword (language name), dropped regardless of
    # position. (A leading bare verb like "Need" is leading-only and survives
    # mid-query — covered by test_leading_verb_stripped_midquery_verb_kept.)
    assert "English" not in extract_query_entities(_REPORTED_QUERY)
    assert "French" not in extract_query_entities("Compare French vs Spanish STT in Whisper")


def test_compliance_acronyms_not_extracted():
    result = extract_query_entities(_REPORTED_QUERY)
    assert "BAA" not in result
    assert "SOC" not in result
    assert "SOC2" not in result


def test_sentence_initial_verb_before_lowercase_stripped():
    # A sentence-initial verb (query start OR after a period) followed by
    # LOWERCASE prose is stripped, so a bare query verb is never a phantom
    # entity. Covers offset 0 and the after-period case (codex IMPL T3).
    assert extract_query_entities("Recommend the best vector database") == []
    assert extract_query_entities("Evaluate the tradeoffs of caching") == []
    assert extract_query_entities("Find a cheaper option") == []
    after_period = extract_query_entities("Compare Whisper and Deepgram. Need fast diarization.")
    assert "Need" not in after_period
    assert "Whisper" in after_period
    assert "Deepgram" in after_period


def test_verb_fronting_capitalized_product_kept_at_every_position():
    # codex IMPL F2/T4/T5: a verb whose continuation is CAPITALIZED fronts a
    # product name and is KEPT — never degraded to a sole generic tail — whether
    # query-leading, opening a later sentence, or mid-clause. Only a lowercase
    # continuation triggers the strip.
    # Query-leading (the T5 case):
    assert extract_query_entities("Review Board vs Gerrit") == ["Review Board", "Gerrit"]
    assert extract_query_entities("Find My Device vs Life360") != ["Device"]
    # Opening a later sentence (the T4 case):
    later = extract_query_entities("Compare location products. Find My Device vs Life360")
    assert "Find" in later
    assert later != ["Device"]
    assert "Review Board" in extract_query_entities("We use AWS. Review Board syncs reviews")
    # Mid-clause:
    assert "Review Board" in extract_query_entities("Compare Review Board vs Gerrit")


def test_reported_query_bare_verb_does_not_hardfail_verifier():
    # codex IMPL T3 counterexample: with "Need" stripped (sentence-initial),
    # a synthesis that uses the word "need" as prose no longer hard-fails when
    # the retained sources lack that word — because "Need" is not an entity.
    entities = extract_query_entities(_REPORTED_QUERY)
    assert "Need" not in entities
    content = (
        "AssemblyAI and Deepgram Nova-3 need diarization support and opt-out "
        "retention for batch calls [1]."
    )
    sources_text = (
        "assemblyai batch transcription retention policy. "
        "deepgram nova-3 diarization compliance overview."
    )
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed


def test_language_names_dropped_but_vendor_survives():
    result = extract_query_entities("Compare French and Spanish support in AssemblyAI")
    assert "French" not in result
    assert "Spanish" not in result
    assert "AssemblyAI" in result


# ============================================================
# No regression: real identifiers still extract
# ============================================================

def test_hyphenated_with_cap_or_digit_still_extracts():
    result = extract_query_entities(
        "compare gpt-4o vs claude-3-5 and Llama-3 and TypeScript-2"
    )
    for entity in ("gpt-4o", "claude-3-5", "Llama-3", "TypeScript-2"):
        assert entity in result


def test_allowlisted_lowercase_hyphenated_still_extracts():
    result = extract_query_entities(
        "compare scikit-learn vs llama-cpp with react-dom styled-components "
        "create-react-app pip-tools npm-run-all"
    )
    for entity in (
        "scikit-learn",
        "llama-cpp",
        "react-dom",
        "styled-components",
        "create-react-app",
        "pip-tools",
        "npm-run-all",
    ):
        assert entity in result


def test_internal_cap_and_dotted_shapes_unchanged():
    assert "vLLM" in extract_query_entities("why does vLLM win")
    result = extract_query_entities("how does llama.cpp use asyncio.gather")
    assert "llama.cpp" in result
    assert "asyncio.gather" in result


def test_sentence_final_hyphenated_identifier_extracts():
    # The exact trailing-"." regression codex T2 corrected: Nova-3 is followed
    # by a sentence period in the reported query and must still extract.
    assert "Nova-3" in extract_query_entities("We benchmarked Deepgram Nova-3.")
    assert "gpt-4o" in extract_query_entities("The winner was gpt-4o.")


# ============================================================
# _is_hyphenated_entity predicate
# ============================================================

def test_is_hyphenated_entity_predicate():
    # cap-or-digit branch
    assert _is_hyphenated_entity("gpt-4o")
    assert _is_hyphenated_entity("Llama-3")
    assert _is_hyphenated_entity("React-Dom")
    # allowlist branch (all-lowercase, no digit)
    assert _is_hyphenated_entity("scikit-learn")
    assert _is_hyphenated_entity("llama-cpp")
    assert _is_hyphenated_entity("LLAMA-CPP")  # allowlist matched via .lower()
    # rejected generic compounds
    assert not _is_hyphenated_entity("opt-out")
    assert not _is_hyphenated_entity("real-time")
    assert not _is_hyphenated_entity("pre-recorded")


def test_documented_residual_lowercase_digit_compound_admitted():
    # KNOWN residual (documented in _is_hyphenated_entity, deferred): a
    # lowercase+digit compound still passes the cap-or-digit test. Eliminating
    # it cleanly needs typed entity metadata. This test pins the residual so a
    # future fix flips it deliberately, not by accident.
    assert _is_hyphenated_entity("tier-2")
    assert "tier-2" in extract_query_entities("compare tier-2 vs tier-3 plans")


def test_four_plus_hyphen_chain_not_partially_matched():
    # Trailing "-" exclusion: even a chain that WOULD pass the gate (caps +
    # digits) must not partial-match its leading 3-hyphen prefix. The old
    # \b-anchored pattern extracted the truncated "A-1-2-3"; the negative
    # lookaround must not.
    result = extract_query_entities("the A-1-2-3-4 build")
    assert "A-1-2-3" not in result
    assert "A-1-2-3-4" not in result


# ============================================================
# codex IMPL review findings (F1: Shape 4 boundary; F2: Review stopword)
# ============================================================

def test_shape4_does_not_resurrect_gated_compound_suffix():
    # F1: Shape 4 must not start after a hyphen and grab the suffix of a
    # gated-out Shape 3 compound. "pre-recorded.wav" → NEITHER "pre-recorded"
    # (gated lowercase compound) NOR "recorded.wav" (Shape 4 suffix) is an
    # entity. The old \b-anchored Shape 4 extracted "recorded.wav".
    result = extract_query_entities("Compare Deepgram for pre-recorded.wav uploads")
    assert "recorded.wav" not in result
    assert "pre-recorded" not in result
    assert "Deepgram" in result


def test_shape4_legit_dotted_paths_still_extract():
    # The Shape 4 boundary tightening must not regress real dotted paths.
    result = extract_query_entities("how does llama.cpp use asyncio.gather and numpy.array")
    assert "llama.cpp" in result
    assert "asyncio.gather" in result
    assert "numpy.array" in result


def test_shape4_sentence_final_dotted_path_extracts():
    # Trailing "." allowed for dotted paths too (sentence-final).
    assert "llama.cpp" in extract_query_entities("We benchmarked llama.cpp.")


def test_review_board_product_name_survives():
    # F2: "Review"/"Reviewing" are NOT stopwords, so a real product name
    # fronted by "Review" survives as a multi-word entity instead of degrading
    # into a generic "Board" bucket that would defeat entity-coverage.
    result = extract_query_entities("Compare Review Board vs Gerrit")
    assert "Review Board" in result
    assert "Board" not in result
    assert "Gerrit" in result


# ============================================================
# Contextual-tier interaction (Shape 5 enable signal)
# ============================================================

def test_dropped_compounds_do_not_enable_contextual_tier():
    # opt-out / real-time are no longer tech-shaped, so with no cue and no real
    # tech entity the contextual lowercase tier stays off → empty result.
    assert extract_query_entities("compare opt-out vs real-time privacy") == []


def test_contextual_tier_enabled_by_hyphenated_digit_entity():
    result = extract_query_entities("gpt-4o on uv runtime")
    assert "gpt-4o" in result
    assert "uv" in result


def test_allowlisted_hyphenated_enables_contextual_tier_without_cue():
    # No CONTEXT_CUE word here; the only enabler is scikit-learn (allowlisted
    # hyphenated → tech-shaped). It must still enable contextual `uv`.
    result = extract_query_entities("scikit-learn alongside uv")
    assert "scikit-learn" in result
    assert "uv" in result


def test_dropped_compound_does_not_enable_contextual_uv():
    # opt-out is gated out of Shape 3 → not tech-shaped → must NOT enable the
    # contextual `uv` tier (no cue present either).
    assert extract_query_entities("opt-out on uv index") == []


# ============================================================
# Verifier integration (entity-coverage hard-fail policy intact)
# ============================================================

def test_verifier_no_hardfail_on_generic_term_now_dropped():
    # opt-out is no longer extracted, so a synthesis discussing it with no
    # source coverage no longer hard-fails. The real entities ARE covered.
    entities = extract_query_entities(_REPORTED_QUERY)
    content = (
        "AssemblyAI and Deepgram Nova-3 both support opt-out of data "
        "retention for privacy-sensitive batch transcription [1]."
    )
    sources_text = (
        "assemblyai batch speech-to-text pricing and retention policy. "
        "deepgram nova-3 diarization and compliance overview."
    )
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures


def test_verifier_softwarns_genuine_uncovered_entity():
    # Entity-coverage is ADVISORY now (codex DESIGN 019e5b0f): a real product
    # discussed + cited but absent from every retained source no longer hard-fails;
    # it ships with a strong "treat as unverified" soft caveat the downstream LLM
    # adjudicates. Structural gates stay hard; this is content grounding, not structure.
    entities = ["Nova-3"]
    content = "Nova-3 leads on diarization accuracy [1]."
    sources_text = "assemblyai and deepgram batch pricing per hour, no model names."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("Nova-3" in w and "UNVERIFIED" in w for w in verdict.soft_warnings)


def test_verifier_passes_when_entities_covered():
    entities = ["AssemblyAI", "Deepgram Nova"]
    content = "AssemblyAI and Deepgram Nova both offer batch STT [1]."
    sources_text = "assemblyai batch transcription docs. deepgram nova compliance and baa."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed


# ============================================================
# Citation-adjacency policy (ISS-20260604-001, codex design session
# 019e721b): a discussed-but-uncovered query entity hard-fails ONLY when it
# carries an in-sentence `[N]` citation (fabricated source attribution).
# An uncovered entity mentioned WITHOUT an in-sentence citation downgrades to
# a soft warning — fixes the coined/internal-label false-positive.
# ============================================================

def test_coined_labels_uncited_uncovered_downgraded_to_soft():
    # The reported production false-positive: an agentic-decision query framed
    # around internal/coined labels. The model reasons ABOUT them in
    # citation-free prose; the cited claim is about the sourced material, not
    # the labels. No retained source can contain the labels (a project
    # codename, a decision-option label, a real lib the search missed), so they
    # are uncovered -> soft warning, NOT a hard fail.
    entities = ["SufiSR", "build-FastMCP-wrapper", "FastMCP"]
    content = (
        "We should build-FastMCP-wrapper for SufiSR rather than adopt FastMCP "
        "wholesale. The MCP wrapper pattern keeps the adapter surface small "
        "and testable [1]."
    )
    sources_text = (
        "model context protocol server design patterns and wrapper tradeoffs. "
        "integration surface considerations for mcp adapters [1]."
    )
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("SufiSR" in w for w in verdict.soft_warnings)


def test_cited_uncovered_entity_softwarns_was_fabricated_attribution():
    # An uncovered entity carrying an IN-SENTENCE citation (the "Prisma is SSPL [3]"
    # shape) is now a STRONG soft caveat, not a hard fail (codex DESIGN 019e5b0f): no
    # discriminator catches a real fabrication without re-capturing the FP classes, so
    # ship + flag "treat as unverified" and let the downstream LLM discount it.
    entities = ["SufiSR"]
    content = "SufiSR ships native multi-tenant isolation out of the box [1]."
    sources_text = "fastmcp wrapper patterns and mcp adapter tradeoffs."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("SufiSR" in w and "UNVERIFIED" in w for w in verdict.soft_warnings)


def test_mixed_sentence_cited_uncovered_softwarns_not_reattributed():
    # codex T1 F2 preserved under the demotion: a joint cited claim "A and B both
    # ... [1]" with B uncovered keeps the STRONG cited caveat on B - the citation is
    # NOT re-attributed to the covered co-entity. FastMCP covered, SufiSR uncovered,
    # shared [1]. Soft now (not hard), but SufiSR still gets the unverified caveat.
    entities = ["FastMCP", "SufiSR"]
    content = "FastMCP and SufiSR both expose a streaming transport [1]."
    sources_text = "fastmcp streaming transport and server api reference [1]."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("SufiSR" in w and "UNVERIFIED" in w for w in verdict.soft_warnings)


def test_uncited_uncovered_factual_claim_downgraded_to_soft():
    # POLICY LOCK (ISS-20260604-001): an UNCITED factual claim about an
    # uncovered REAL entity is a deliberate recall trade — it downgrades from
    # hard fail to soft warning, because it is structurally indistinguishable
    # from coined-label framing and carries no fabricated source attribution.
    # Pins the policy so a future change flips it deliberately, not by accident.
    # Contrast test_verifier_still_hardfails_genuine_uncovered_entity (CITED).
    entities = ["Nova-3"]
    content = "Nova-3 leads on diarization accuracy."  # note: no [N] marker
    sources_text = "assemblyai and deepgram batch pricing per hour, no model names."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("Nova-3" in w for w in verdict.soft_warnings)


def test_entity_has_adjacent_citation_is_sentence_scoped():
    # The citation must co-occur with the entity IN THE SAME SENTENCE. A
    # citation in a different sentence does not make the entity "cited".
    from src.synthesis.output_verifier import _entity_has_adjacent_citation

    same = "Nova-3 wins on accuracy [1]."
    assert _entity_has_adjacent_citation(same.lower(), "nova-3")
    cross = "Nova-3 wins on accuracy. AssemblyAI is cheaper [1]."
    assert not _entity_has_adjacent_citation(cross.lower(), "nova-3")


# ============================================================
# Descriptive query vocabulary precision (ISS-20260606-001): evaluative
# adjectives ("Optimal") and acronym-prefixed compounds ("AI-served") are query
# MODIFIERS, not entities. They are no longer extracted, so they can no longer
# phantom-hard-fail the coverage gate by tripping the v0.3.8 citation-adjacency
# split on spurious in-sentence citation co-occurrence. EXTRACTION fix — the
# verifier verdict logic is unchanged.
# ============================================================

# The reported production query (leading-capital, otherwise normal case — NOT a
# fully Title-Cased string). Reproduced the exact failing entity set against the
# live v0.3.8 verifier: ['Optimal', 'Cloudflare Pages', 'ClaudeBot', 'AI-served'].
_REPORTED_HOSTING_QUERY = (
    "Optimal hosting for AI-served static sites on Cloudflare Pages and "
    "ClaudeBot crawler access"
)


def test_evaluative_adjective_not_extracted():
    # "Optimal" is the reported case; spot-check a few siblings. Each is the
    # leading capitalized word, so Shape 1 would otherwise promote it.
    assert "Optimal" not in extract_query_entities("Optimal hosting for static sites")
    assert "Fastest" not in extract_query_entities("Fastest CDN for video delivery")
    assert "Cheapest" not in extract_query_entities("Cheapest object storage tier")
    assert "Scalable" not in extract_query_entities("Scalable queue for event streams")


def test_descriptive_acronym_compound_not_extracted():
    # "AI-served" is the reported case. Both casings drop (lowercase tail and
    # Title-Case tail), plus the common sibling forms.
    assert "AI-served" not in extract_query_entities("hosting for AI-served sites")
    assert "AI-Served" not in extract_query_entities("Hosting for AI-Served Sites")
    assert "ML-based" not in extract_query_entities("compare ML-based routers")
    assert "LLM-driven" not in extract_query_entities("an LLM-driven pipeline")
    assert "API-first" not in extract_query_entities("an API-first platform")


def test_reported_hosting_query_yields_only_real_entities():
    # End-to-end extraction: Optimal (evaluative) + AI-served (acronym compound)
    # gone; the real entities survive. ("hosting"/"static"/"sites"/"crawler"/
    # "access" are lowercase in the reported casing, so Shape 1 never sees them —
    # a FULLY Title-Cased query that capitalizes those nouns is a broader,
    # separate residual, out of scope for this fix.)
    entities = extract_query_entities(_REPORTED_HOSTING_QUERY)
    assert "Optimal" not in entities
    assert "AI-served" not in entities
    assert "Cloudflare Pages" in entities
    assert "ClaudeBot" in entities


def test_descriptive_vocab_no_phantom_hardfail_end_to_end():
    # The headline regression: a correct, well-cited synthesis whose query
    # carried "Optimal" + "AI-served" no longer phantom-hard-fails. Isolates the
    # two fixed FP sources (no ClaudeBot — a real entity uncovered by exact token
    # is a separate surface-form problem, out of scope). Pre-fix this query
    # extracted ['Optimal', 'Cloudflare Pages', 'AI-served'] and hard-failed on
    # Optimal + AI-served via citation adjacency.
    query = "Optimal hosting for AI-served static sites on Cloudflare Pages"
    entities = extract_query_entities(query)
    assert entities == ["Cloudflare Pages"]
    content = (
        "The optimal hosting choice for AI-served static sites is Cloudflare "
        "Pages [1]. Cloudflare Pages serves pre-rendered HTML at the edge [1]."
    )
    sources_text = (
        "cloudflare pages is a static site host with global edge serving and "
        "pre-rendered html output."
    )
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures


def test_is_hyphenated_entity_drops_descriptive_acronym_compounds():
    # Predicate-level: both casings of the descriptive tail drop.
    assert not _is_hyphenated_entity("AI-served")
    assert not _is_hyphenated_entity("AI-Served")
    assert not _is_hyphenated_entity("ML-based")
    assert not _is_hyphenated_entity("LLM-Driven")
    assert not _is_hyphenated_entity("GPU-accelerated")
    assert not _is_hyphenated_entity("API-first")


def test_is_hyphenated_entity_preserves_real_acronym_identifiers():
    # Acronym TAIL (all-caps) -> real identifier, kept.
    assert _is_hyphenated_entity("AI-SDK")
    assert _is_hyphenated_entity("AI-API")
    # Digit tail -> real identifier, kept.
    assert _is_hyphenated_entity("AI-2027")
    # Non-acronym prefix -> guard skipped, cap test keeps it.
    assert _is_hyphenated_entity("Web-LLM")
    # Pre-existing identifiers unaffected by the new guard.
    assert _is_hyphenated_entity("gpt-4o")
    assert _is_hyphenated_entity("claude-3-5")
    assert _is_hyphenated_entity("scikit-learn")


def test_multiword_entity_opening_with_evaluative_adjective_preserved():
    # codex T6 M1: a multi-word entity that OPENS with an evaluative adjective is
    # PRESERVED WHOLE — the adjective is filtered only as a STANDALONE token, not
    # via the phrase splitter. Real companies must survive, not degrade to a weak
    # generic fragment ("Capital" / "Dynamics" / "Robotics" / "Buy").
    assert "Best Buy" in extract_query_entities("compare Best Buy returns policy")
    assert "Scalable Capital" in extract_query_entities("Scalable Capital vs Trade Republic")
    assert "Optimal Dynamics" in extract_query_entities("Optimal Dynamics route optimization")
    assert "Reliable Robotics" in extract_query_entities("Reliable Robotics autonomous aircraft")
    # The bare generic fragment is NOT emitted in place of the whole entity.
    assert "Capital" not in extract_query_entities("Scalable Capital vs Trade Republic")
    # "Better" stays excluded from the stoplist (so "Better Stack" is unaffected).
    assert "Better Stack" in extract_query_entities("compare Better Stack vs Datadog")


def test_standalone_evaluative_adjective_still_dropped():
    # The standalone case the filter targets: a leading evaluative adjective with
    # lowercase prose after it is dropped (not part of a capitalized phrase).
    assert "Optimal" not in extract_query_entities("Optimal hosting for static sites")
    assert "Scalable" not in extract_query_entities("Scalable queues for events")
    assert "Best" not in extract_query_entities("Best vector database for RAG")


def test_acronym_compound_with_propernoun_tail_preserved():
    # codex T6 M2: the curated-tail gate preserves real hyphenated identifiers
    # whose tail is a ProperNoun or ecosystem name (NOT a descriptive participle).
    assert "AR-Foundation" in extract_query_entities("Compare AR-Foundation vs WebXR")
    assert "AI-Horde" in extract_query_entities("Compare AI-Horde vs Web-LLM")
    assert "gRPC-Web" in extract_query_entities("Compare gRPC-Web vs REST")
    # Predicate-level mirror.
    assert _is_hyphenated_entity("AR-Foundation")
    assert _is_hyphenated_entity("AI-Horde")
    assert _is_hyphenated_entity("gRPC-Web")


def test_claudebot_alias_residual_now_soft_under_demotion():
    # codex T6 M3 originally PINNED this as a residual HARD fail: `ClaudeBot` cited
    # but named in sources only by an alias ("Anthropic's web crawler") that
    # exact-token coverage misses. The entity-coverage demotion (codex DESIGN
    # 019e5b0f) resolves the residual wholesale - it is now a soft caveat, not a hard
    # fail, so the alias-normalization gap can no longer break a synthesis.
    entities = ["ClaudeBot"]
    content = "Cloudflare blocks ClaudeBot by default, so allowlist it [1]."
    sources_text = "cloudflare waf can allow or block anthropic's web crawler via skip rules [1]."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed
    assert not verdict.hard_failures
    assert any("ClaudeBot" in w for w in verdict.soft_warnings)


# ============================================================
# Promotion integration (entity-balanced safety net consumer)
# ============================================================

def test_promotion_buckets_real_entities_not_junk_compounds():
    # entity_balanced promotion iterates extract_query_entities(query). With
    # junk compounds gone, only real entities (incl. allowlisted hyphenated)
    # create promotion buckets; a junk-only source stays rejected.
    sources = [
        _make_source("XGBoost Deep Dive", "xgboost is great, all about xgboost here"),
        _make_source("scikit-learn brief", "scikit-learn barely covered here"),
        _make_source("real-time filler", "real-time streaming filler with no vendor"),
    ]
    scores = [0.9, 0.4, 0.35]
    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(
        gate.evaluate("compare XGBoost vs scikit-learn for real-time", sources)
    )

    assert result.decision == QualityDecision.PARTIAL
    good_titles = [s.title for s in result.good_sources]
    assert "XGBoost Deep Dive" in good_titles          # passing vendor source
    assert "scikit-learn brief" in good_titles         # allowlisted hyphenated → promoted
    assert "real-time filler" not in good_titles        # junk compound → no bucket
    assert "real-time filler" in [s.title for s in result.rejected_sources]


# ============================================================
# Allowlist hygiene
# ============================================================

def test_lowercase_hyphenated_allowlist_shape_and_disjointness():
    assert isinstance(LOWERCASE_HYPHENATED_TOOL_ALLOWLIST, frozenset)
    for name in LOWERCASE_HYPHENATED_TOOL_ALLOWLIST:
        # all-lowercase + hyphenated + no digit — names with a cap/digit don't
        # need the allowlist (they pass the cap-or-digit predicate directly).
        assert name == name.lower()
        assert "-" in name
        assert not any(ch.isdigit() for ch in name)
    # No overlap with the single-word tool allowlists.
    assert not (LOWERCASE_HYPHENATED_TOOL_ALLOWLIST & LOWERCASE_TOOL_ALLOWLIST)
    assert not (LOWERCASE_HYPHENATED_TOOL_ALLOWLIST & CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST)
