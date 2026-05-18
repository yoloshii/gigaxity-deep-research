"""Regression tests for BACKLOG Items 6 + 7 cleanup (codex DESIGN session
019e3a66-313d-7121-b52f-541165732859, NONCE
codex-design-items-6-7-2026-05-18-7e3a9c4b).

Covers:
- Item 7: Shape 5 lowercase-tool allowlist in extract_query_entities,
  including always-safe + contextual tiers, case sensitivity,
  hyphenated/dotted dedupe, and integration with the verifier entity-
  coverage hard-fail path.
- Item 6: Abbreviation-aware sentence splitter in sentence_utils,
  preventing false-fails when synthesis output contains U.S./e.g./i.e./
  Mr./Dr./etc., while preserving false-pass guards.

Test file is shared across both items per the locked design ladder
(steps 3 and 7).
"""

from src.synthesis import (
    extract_query_entities,
    verify_synthesis_output,
)
from src.synthesis.entity_allowlist import (
    CONTEXT_CUES,
    CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST,
    LOWERCASE_TOOL_ALLOWLIST,
)
from src.synthesis.sentence_utils import (
    protect_abbreviations,
    restore_abbreviations,
    split_sentences,
)
from src.synthesis.verification import extract_claims_with_citations


# ============================================================
# Item 7 — Shape 5: lowercase-tool allowlist
# ============================================================


# ---- Shape 1-4 regression: nothing pre-existing should break ----

def test_shape1_capitalized_still_extracts():
    """Shape 1 (capitalized words) unchanged by Shape 5 addition."""
    assert extract_query_entities("Compare Tavily and LinkUp") == ["Tavily", "LinkUp"]


def test_shape2_internal_cap_still_extracts():
    """Shape 2 (internal-cap identifiers like vLLM) unchanged."""
    result = extract_query_entities("why does vLLM outperform iOS")
    assert "vLLM" in result
    assert "iOS" in result


def test_shape3_hyphenated_still_extracts():
    """Shape 3 (hyphenated identifiers like gpt-4o) unchanged."""
    result = extract_query_entities("compare gpt-4o vs claude-3-5")
    assert "gpt-4o" in result
    assert "claude-3-5" in result


def test_shape4_dotted_still_extracts():
    """Shape 4 (dotted module paths like llama.cpp) unchanged."""
    result = extract_query_entities("how does llama.cpp use asyncio.gather")
    assert "llama.cpp" in result
    assert "asyncio.gather" in result


# ---- Shape 5: always-safe lowercase tools ----

def test_shape5_bun_extracts():
    """bun is in LOWERCASE_TOOL_ALLOWLIST and detected without context."""
    assert "bun" in extract_query_entities("how fast is bun")


def test_shape5_npm_extracts():
    assert "npm" in extract_query_entities("which version of npm is current")


def test_shape5_deno_extracts():
    assert "deno" in extract_query_entities("deno permissions model explained")


def test_shape5_pnpm_extracts():
    assert "pnpm" in extract_query_entities("does pnpm save disk")


def test_shape5_pip_extracts():
    """pip is always-safe; detected even without comparison cues."""
    assert "pip" in extract_query_entities("use pip to install dependencies")


def test_shape5_compare_bun_vs_npm():
    """The canonical 'compare X vs Y' query that v0.3.0 BACKLOG flagged."""
    result = extract_query_entities("compare bun vs npm")
    assert "bun" in result
    assert "npm" in result


def test_shape5_multiple_always_safe_tools():
    """Multiple always-safe tools all surface in text order."""
    result = extract_query_entities("docker, kubectl, helm, terraform")
    assert result == ["docker", "kubectl", "helm", "terraform"]


# ---- Shape 5: contextual (ambiguous) tier ----

def test_shape5_contextual_rust_without_cue_is_suppressed():
    """'remove rust from metal' has no tech context — `rust` is the metal,
    not the language. Shape 5 must NOT extract it."""
    assert extract_query_entities("remove rust from metal") == []


def test_shape5_contextual_go_without_cue_is_suppressed():
    """'how to go faster' has no tech context — `go` is a verb."""
    assert extract_query_entities("how to go faster") == []


def test_shape5_contextual_make_without_cue_is_suppressed():
    """'how to make a table' has no tech context — `make` is a verb."""
    assert extract_query_entities("how to make a table") == []


def test_shape5_contextual_uv_without_cue_is_suppressed():
    """'the uv index is high today' is meteorological, not Python tooling."""
    assert extract_query_entities("the uv index is high today") == []


def test_shape5_contextual_rust_with_compare_cue_extracts():
    """'compare go vs rust' has the comparison cue — both extract."""
    result = extract_query_entities("compare go vs rust")
    assert "go" in result
    assert "rust" in result


def test_shape5_contextual_uv_with_install_cue_extracts():
    """`install` is a tech cue → enables contextual `uv`."""
    result = extract_query_entities("install uv for python")
    assert "uv" in result


def test_shape5_contextual_enabled_by_shape234_tech_entity():
    """Per Q9 + codex T10 HIGH refinement: contextual tier enabled when
    shapes 2-4 (NOT shape 1) extracted a tech-shaped entity. Shape 1
    catches arbitrary capitalized proper nouns (Bob, Alice) so it cannot
    safely enable the contextual tier — only the inherently-tech
    shapes 2-4 do.
    `gpt-4o` is Shape 3 (hyphenated); enables contextual `uv`."""
    result = extract_query_entities("gpt-4o on uv runtime")
    assert "uv" in result
    assert "gpt-4o" in result


def test_shape5_contextual_NOT_enabled_by_shape1_proper_noun():
    """Codex T10 HIGH regression: Shape 1 alone does NOT enable
    contextual. Was the bug — `Bob` (shape 1 capitalized) used to enable
    `make` (contextual lowercase). Must NOT happen post-fix."""
    result = extract_query_entities("What did Bob make for dinner?")
    assert "make" not in result
    # Shape 1 captures `Bob` as a 3-letter capitalized name (still
    # acceptable — the bug was the CONTEXTUAL leak, not Shape 1 itself).
    assert "Bob" in result


def test_shape5_contextual_NOT_enabled_by_shape1_alice_go():
    """Codex T10 HIGH regression #2."""
    result = extract_query_entities("How does Alice go faster?")
    assert "go" not in result
    assert "Alice" in result


def test_shape5_contextual_NOT_enabled_by_shape1_taylor_swift():
    """Codex T10 HIGH regression #3 — multi-word Shape 1 entity."""
    result = extract_query_entities("Did Taylor Swift make an announcement?")
    assert "make" not in result


def test_shape5_contextual_disabled_without_cue_or_entity():
    """Sanity: no cue, no shape-1-4 entity → contextual tier off."""
    # `tar` is contextual-only. Plain prose, no cue → suppressed.
    assert extract_query_entities("wrap tar around the pole") == []


# ---- Shape 5: case sensitivity ----

def test_shape5_uppercase_pip_not_lowercase_folded():
    """'PIP' (proper noun, e.g. private investment plan) goes through
    Shape 1 as capitalized, NOT through Shape 5 as `pip`."""
    result = extract_query_entities("PIP is the Procter Investment Plan")
    assert "PIP" in result
    # Verify no lowercase duplicate
    assert "pip" not in result


def test_shape5_mixed_case_bun_goes_through_shape1():
    """'Bun' (capitalized) goes through Shape 1, not Shape 5."""
    result = extract_query_entities("Bun is a JavaScript runtime")
    assert "Bun" in result
    assert "bun" not in result


def test_shape5_pattern_is_case_sensitive_lowercase_only():
    """Shape 5 pattern (no re.IGNORECASE) matches only exact lowercase
    form. Uppercase or mixed-case forms route through Shape 1 (if 3+
    chars and capitalized). When BOTH forms appear in the same query,
    the case-insensitive dedupe (Q11) suppresses the Shape 5 emission
    because the capitalized Shape 1 hit already covers the same entity."""
    # Pure lowercase only → Shape 5 fires.
    assert "npm" in extract_query_entities("compare npm and yarn")
    # Pure uppercase only → Shape 1 catches NPM as capitalized; Shape 5
    # case-sensitive pattern does not match `NPM` against lowercase `npm`.
    result_upper = extract_query_entities("compare NPM and YARN")
    assert "NPM" in result_upper
    assert "npm" not in result_upper  # case-sensitive: no lowercase fold
    # Capitalized form alone goes through Shape 1, NOT Shape 5.
    result_cap = extract_query_entities("Npm versus Yarn")
    assert "Npm" in result_cap
    assert "Yarn" in result_cap
    assert "npm" not in result_cap  # dedupe vs Shape 1 hit


# ---- Shape 5: hyphenated/dotted collision (Q11) ----

def test_shape5_no_bare_pip_inside_pip_tools():
    """Per Q11: bare allowlist entity inside a hyphenated identifier
    does NOT re-emit. `pip-tools` is the canonical entity."""
    result = extract_query_entities("what is pip-tools used for")
    assert "pip-tools" in result
    assert "pip" not in result  # NOT re-emitted from the substring


def test_shape5_no_bare_npm_inside_npm_run_all():
    """Same dedupe rule for `npm-run-all`."""
    result = extract_query_entities("using npm-run-all for parallel scripts")
    assert "npm-run-all" in result
    assert "npm" not in result


def test_shape5_standalone_pip_after_hyphenated_still_extracts():
    """When `pip` appears standalone elsewhere in the same query,
    it still extracts — only the substring inside the hyphenated form
    is suppressed."""
    result = extract_query_entities("pip-tools is a wrapper around pip")
    assert "pip-tools" in result
    assert "pip" in result  # standalone occurrence


def test_shape5_no_emission_inside_dotted_path():
    """Bare `npm` inside hypothetical `mypackage.npm` does NOT emit."""
    # Use a real dotted-path-shaped string that contains an allowlist token.
    result = extract_query_entities("what is asyncio.gather doing")
    assert "asyncio.gather" in result
    # No false-positive: even though 'gather' isn't in the allowlist, also
    # verify a real allowlist member inside a dotted path doesn't emit:
    result2 = extract_query_entities("see numpy.pip module")
    assert "numpy.pip" in result2
    assert "pip" not in result2


# ---- Shape 5: allowlist constants surface ----

def test_lowercase_tool_allowlist_is_frozenset():
    """Constants are frozenset for immutability."""
    assert isinstance(LOWERCASE_TOOL_ALLOWLIST, frozenset)
    assert isinstance(CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST, frozenset)
    assert isinstance(CONTEXT_CUES, frozenset)


def test_lowercase_tool_allowlist_seed_members():
    """The codex-design-locked seed members are present."""
    for tool in ("bun", "npm", "deno", "pnpm", "pip", "yarn", "cargo"):
        assert tool in LOWERCASE_TOOL_ALLOWLIST


def test_contextual_allowlist_seed_members():
    """The contextual-tier seed members are present."""
    for tool in ("uv", "go", "rust", "tar", "make", "mix", "gem"):
        assert tool in CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST


def test_context_cues_seed_members():
    for cue in ("compare", "vs", "versus", "install", "benchmark", "runtime"):
        assert cue in CONTEXT_CUES


def test_no_overlap_between_safe_and_contextual_allowlists():
    """A tool name belongs in exactly one tier — no ambiguity."""
    assert not (LOWERCASE_TOOL_ALLOWLIST & CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST)


# ---- Shape 5: verifier integration ----

def test_verifier_hard_fails_uncovered_bun_npm():
    """The canonical Item 7 scenario: 'compare bun vs npm' extracts
    both as entities; if the verifier sees `npm` discussed in the
    synthesis but absent from sources, it must hard-fail."""
    entities = extract_query_entities("compare bun vs npm")
    assert entities == ["bun", "npm"]
    verdict = verify_synthesis_output(
        content="bun is faster than npm in cold-start benchmarks. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=entities,
        sources_text="bun documentation describes startup benchmarks",
    )
    assert not verdict.passed
    assert any("npm" in f for f in verdict.hard_failures)


def test_verifier_passes_when_both_covered():
    """Positive control: both entities grounded in sources → passes."""
    entities = extract_query_entities("compare bun vs npm")
    verdict = verify_synthesis_output(
        content="bun and npm both ship JS runtimes. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=entities,
        sources_text="bun docs say it is fast. npm docs say it is mature.",
    )
    assert verdict.passed


def test_verifier_lowercase_entity_matches_pre_lowercased_sources():
    """The verifier's `sources_text` parameter is contract-required to
    be pre-lowercased by callers (output_verifier.py:125 docstring), so
    the entity-coverage check uses byte-level token matching. When the
    caller honors the contract, lowercase entities like `pip` match
    naturally."""
    entities = extract_query_entities("how to use pip cleanly")
    assert "pip" in entities
    verdict = verify_synthesis_output(
        content="Use pip with constraint files. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=entities,
        # Caller lowercased per contract.
        sources_text="pip supports constraint files via -c flag.",
    )
    assert verdict.passed


# ---- Q18 risk regression: ambiguous-token false-positive guards ----

def test_risk_q18_how_to_go_faster_extracts_nothing():
    """Q18 named regression. `go` alone is not enough."""
    assert extract_query_entities("how to go faster") == []


def test_risk_q18_how_to_make_a_table_extracts_nothing():
    """Q18 named regression. `make` alone is not enough."""
    assert extract_query_entities("how to make a table") == []


def test_risk_q18_remove_rust_from_metal_extracts_nothing():
    """Q18 named regression. `rust` alone is not enough."""
    assert extract_query_entities("remove rust from metal") == []


# ============================================================
# Item 6 — Abbreviation-aware sentence splitter
# ============================================================


# ---- sentence_utils unit tests ----

def test_protect_restore_roundtrip_preserves_content():
    """Protect followed by restore yields the original string."""
    original = "The U.S. has e.g. Mr. Smith working at Acme Inc."
    assert restore_abbreviations(protect_abbreviations(original)) == original


def test_protect_preserves_original_casing():
    """Abbreviation casing in the input is preserved through protection.
    `U.S.` stays `U.S.` (with sentinel inside) — not lowercased."""
    text = "The U.S. economy"
    protected = protect_abbreviations(text)
    # The `.` chars in U.S. are replaced; the letters U and S are unchanged.
    assert "U" in protected
    assert "S" in protected
    # And restore brings the periods back without touching the letters.
    assert restore_abbreviations(protected) == text


def test_protect_is_case_insensitive():
    """Lowercase, uppercase, and mixed-case abbreviations all protected."""
    for variant in ("u.s.", "U.S.", "u.S."):
        protected = protect_abbreviations(f"The {variant} did it")
        # No `.` chars survive inside the protected abbreviation
        assert ". " not in protected.replace(" did", "")


def test_split_sentences_empty_returns_empty_list():
    assert split_sentences("") == []


def test_split_sentences_no_abbreviations_matches_old_behavior():
    """Without abbreviations, behaves like the old `_SENTENCE_SPLIT`."""
    text = "First sentence. Second sentence! Third? Fourth."
    pieces = split_sentences(text)
    # Trailing terminator is consumed but the last "Fourth." stays as
    # "Fourth." because there's no trailing whitespace after the period.
    assert "First sentence" in pieces
    assert "Second sentence" in pieces
    assert "Third" in pieces


def test_split_sentences_abbreviation_does_not_break_sentence():
    """A sentence containing `U.S.` stays intact rather than splitting."""
    text = "The U.S. has X. Then Y happens."
    pieces = split_sentences(text)
    # First sentence should include `U.S.` whole, not be split at `U.S`.
    # After abbreviation protection, the first terminator the splitter sees
    # is the period after `X`.
    assert any("U.S." in p for p in pieces)
    assert any("Y happens" in p for p in pieces)


def test_split_sentences_eg_does_not_break_sentence():
    """`e.g.` mid-sentence does not introduce a fake sentence boundary."""
    text = "Use a runtime, e.g. bun, for cold starts. Then deploy."
    pieces = split_sentences(text)
    # Find the piece containing e.g.; it should also contain `bun`.
    eg_piece = next((p for p in pieces if "e.g." in p), None)
    assert eg_piece is not None
    assert "bun" in eg_piece


def test_split_sentences_honorifics_do_not_break():
    """Honorifics (Mr., Dr.) do not introduce false sentence boundaries."""
    text = "Mr. Smith and Dr. Jones disagree on bun."
    pieces = split_sentences(text)
    # The whole text is one sentence — find a piece containing all three.
    full = next((p for p in pieces if "Mr." in p), None)
    assert full is not None
    assert "Dr." in full
    assert "bun" in full


# ---- Verifier integration: false-fail elimination ----

def _verify_with_entities(content, entities, sources_text):
    """Test helper — call verifier with the entity-coverage path enabled."""
    return verify_synthesis_output(
        content=content,
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=entities,
        sources_text=sources_text,
    )


def test_item6_false_fail_eliminated_us_market_linkup():
    """Codex Q6 case 1: gap phrase before abbreviation, entity after it.
    Pre-fix, the splitter saw `no source for u` / `s. market coverage of
    linkup` and would not find a sentence framing `linkup`, false-failing.
    Post-fix, u.s. stays whole and the sentence frames the gap.
    sources_text must NOT contain the entity, so the verifier actually
    enters the gap-framing branch instead of short-circuiting on coverage."""
    verdict = _verify_with_entities(
        content="no source for u.s. market coverage of linkup.",
        entities=["LinkUp"],
        sources_text="generic documentation for tavily and serper search apis",
    )
    assert verdict.passed, f"Should pass via gap framing, got: {verdict.hard_failures}"


def test_item6_false_fail_eliminated_eg_benchmark_npm():
    """Codex Q6 case 2: e.g. abbreviation between gap phrase and entity."""
    verdict = _verify_with_entities(
        content="could not find e.g. benchmark data for npm.",
        entities=["npm"],
        sources_text="generic documentation for cargo and pip and yarn",
    )
    assert verdict.passed, f"Should pass via gap framing, got: {verdict.hard_failures}"


def test_item6_false_fail_eliminated_mr_smith_bun():
    """Codex Q6 case 3: honorific abbreviation between gap phrase and entity."""
    verdict = _verify_with_entities(
        content="no available source for mr. smith's bun benchmark.",
        entities=["bun"],
        sources_text="generic documentation for node and deno and pnpm",
    )
    assert verdict.passed, f"Should pass via gap framing, got: {verdict.hard_failures}"


# ---- Verifier integration: false-pass guards (Q18 risk regression) ----

def test_item6_false_pass_guard_us_then_uncovered_npm():
    """Codex Q6 false-pass case 1: gap-framed abbreviation sentence
    followed by an uncovered entity sentence — must STILL hard-fail.
    This guards against over-merging sentences (the main Item 6 risk
    per Q18)."""
    verdict = _verify_with_entities(
        content="no source covers u.s. market size. npm adoption is 80%.",
        entities=["npm"],
        # sources_text must NOT contain "npm" as a token; describe other tools.
        sources_text="generic documentation for cargo and yarn package managers",
    )
    assert not verdict.passed, "Uncovered npm in second sentence must hard-fail"
    assert any("npm" in f for f in verdict.hard_failures)


def test_item6_false_pass_guard_eg_then_uncovered_bun():
    """Codex Q6 false-pass case 2: e.g. abbreviation in framed sentence,
    bun in a different uncovered sentence."""
    verdict = _verify_with_entities(
        content="no source covers e.g. package managers. bun is faster.",
        entities=["bun"],
        sources_text="generic documentation for npm and pnpm and yarn",
    )
    assert not verdict.passed, "Uncovered bun in second sentence must hard-fail"
    assert any("bun" in f for f in verdict.hard_failures)


def test_item6_false_pass_guard_dr_then_uncovered_deno():
    """Codex Q6 false-pass case 3: honorific in framed sentence, deno
    in a different uncovered sentence."""
    verdict = _verify_with_entities(
        content="no documentation is available for dr. smith. deno supports permissions.",
        entities=["deno"],
        sources_text="generic documentation for node and bun runtimes",
    )
    assert not verdict.passed, "Uncovered deno in second sentence must hard-fail"
    assert any("deno" in f for f in verdict.hard_failures)


# ---- Claim-extractor integration (verification.py:263) ----

class _MockSource:
    """Minimal source-like object for extract_claims_with_citations."""
    def __init__(self, content: str):
        self.content = content


def test_claim_extractor_preserves_us_abbreviation():
    """Pre-fix, `The U.S. has X.[1]` extracted only ` has X.` as the claim,
    truncating `The U.S.` because the regex broke at the first period.
    Post-fix, the full claim `The U.S. has X.` is preserved."""
    sources = [_MockSource("evidence for U.S. claim")]
    text = "The U.S. has X.[1]"
    results = extract_claims_with_citations(text, sources)
    assert len(results) == 1
    claim, _evidence, source_num = results[0]
    assert "U.S." in claim
    assert claim.strip().startswith("The U.S.")
    assert source_num == 1


def test_claim_extractor_preserves_eg_abbreviation():
    """`Use a runtime, e.g. bun.[1]` keeps `e.g.` inside the claim."""
    sources = [_MockSource("evidence for bun")]
    text = "Use a runtime, e.g. bun.[1]"
    results = extract_claims_with_citations(text, sources)
    assert len(results) == 1
    claim, _evidence, _ = results[0]
    assert "e.g." in claim
    assert "bun" in claim


def test_claim_extractor_preserves_mr_abbreviation():
    """`Mr. Smith uses bun.[1]` keeps `Mr.` inside the claim."""
    sources = [_MockSource("evidence for Smith and bun")]
    text = "Mr. Smith uses bun.[1]"
    results = extract_claims_with_citations(text, sources)
    assert len(results) == 1
    claim, _evidence, _ = results[0]
    assert "Mr." in claim
    assert "bun" in claim


def test_claim_extractor_multi_claim_with_abbreviations():
    """Two claims, both with abbreviations, both citations preserved."""
    sources = [
        _MockSource("evidence A"),
        _MockSource("evidence B"),
    ]
    text = "The U.S. has X.[1] Then e.g. bun runs.[2]"
    results = extract_claims_with_citations(text, sources)
    assert len(results) == 2
    assert "U.S." in results[0][0]
    assert "e.g." in results[1][0]
    assert results[0][2] == 1
    assert results[1][2] == 2
