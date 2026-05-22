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


def test_verifier_still_hardfails_genuine_uncovered_entity():
    # Target hallucination class preserved: a real product discussed in the
    # synthesis but absent from every retained source still hard-fails.
    entities = ["Nova-3"]
    content = "Nova-3 leads on diarization accuracy [1]."
    sources_text = "assemblyai and deepgram batch pricing per hour, no model names."
    verdict = _verify(content, entities, sources_text)
    assert not verdict.passed
    assert any("Nova-3" in f for f in verdict.hard_failures)


def test_verifier_passes_when_entities_covered():
    entities = ["AssemblyAI", "Deepgram Nova"]
    content = "AssemblyAI and Deepgram Nova both offer batch STT [1]."
    sources_text = "assemblyai batch transcription docs. deepgram nova compliance and baa."
    verdict = _verify(content, entities, sources_text)
    assert verdict.passed


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
