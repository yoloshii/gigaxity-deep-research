"""Verifier: entity-coverage is ADVISORY (soft), never a hard gate.

`synthesize` compresses sources and ships to a downstream LLM. A false-positive
entity-coverage hard fail destroyed 100% of that compression (the caller fell
back to raw sources) - and "absent query entity + adjacent citation" produced
three distinct false-positive classes: all-caps framing, lexical surface
variants, and legitimately-absent specific entities (a "Master of Artificial
Intelligence" degree the generic sources never name). Per the codex DESIGN pass
(019e5b0f) and the June-2026 grounding literature (grounding is a per-claim
ADVISORY signal, not an answer-level pass/fail), entity-coverage is DEMOTED to a
soft warning: the synthesis always ships, the caveat rides along, the downstream
LLM adjudicates. Structural gates (empty / reasoning-only / truncated /
subcall-failed / zero-citations) stay hard.

The surface-variant carve-out (alias map + version suffix) survives ONLY to
graduate the warning TEXT - a known alias/variant gets the precise "surface-form
variant present" note; everything else gets the stronger "treat as unverified"
caveat. It no longer affects pass/fail.
"""
from src.llm_utils import LLMOutput
from src.synthesis.output_verifier import annotate_with_verdict, verify_synthesis_output


def _cov(content, query_entities, sources_text, cited_count=1, source_count=1):
    return verify_synthesis_output(
        content=content,
        llm_output=None,
        cited_count=cited_count,
        source_count=source_count,
        query_entities=query_entities,
        sources_text=sources_text,
    )


# --- entity-coverage NEVER hard-fails (the demotion) ---

def test_fabricated_proper_noun_is_soft_not_hard():
    """The old hard-fail fabrication shape ("Prisma is SSPL [1]", no "prisma" in
    sources) now PASSES with a strong soft caveat - codex: there is no discriminator
    that catches a real fabrication without re-capturing the FP classes, so the right
    behavior is ship + a strong caveat the downstream LLM adjudicates."""
    v = _cov(
        "Prisma relicensed to SSPL last year [1].",
        ["Prisma"],
        "a source about orm tooling with no vendor named",
    )
    assert v.passed
    assert not v.hard_failures
    assert any("Prisma" in w and "UNVERIFIED" in w for w in v.soft_warnings)


def test_legitimately_absent_specific_entity_is_soft():
    """The real reported trigger: a Master-of-Artificial-Intelligence eligibility
    query. The synthesis correctly discusses "Artificial Intelligence" but the
    generic government sources never name the degree - the absence IS the finding,
    not a fabrication. Soft, passes (was a false-positive hard fail)."""
    v = _cov(
        "No retained page confirms the Master of Artificial Intelligence is approved [1].",
        ["Artificial Intelligence"],
        "services australia austudy eligibility for approved courses only",
    )
    assert v.passed
    assert not v.hard_failures
    assert any("Artificial Intelligence" in w for w in v.soft_warnings)


def test_acronym_standards_absent_are_soft():
    """SOC 2 / ISO 27001 / EU AI ACT / HIPAA BAA / PCI DSS absent from sources ->
    soft, passes (formerly hard)."""
    for std in ("SOC 2", "ISO 27001", "EU AI ACT", "HIPAA BAA", "PCI DSS"):
        v = _cov(
            f"The platform is {std} certified [1].",
            [std],
            "a source about platform features with no certification named",
        )
        assert v.passed, f"{std} should pass (soft)"
        assert not v.hard_failures, f"{std} should not hard-fail"
        assert any(std in w for w in v.soft_warnings), f"{std} should be soft-warned"


def test_docker_engine_only_head_token_present_is_soft():
    """Head-token-only coverage ("docker" in "docker desktop", no alias/variant) ->
    soft, passes (formerly hard)."""
    v = _cov(
        "The native Docker Engine bypasses vpnkit [1].",
        ["Docker Engine"],
        "docker desktop bundles a managed linux vm",
    )
    assert v.passed
    assert not v.hard_failures
    assert any("Docker Engine" in w for w in v.soft_warnings)


def test_titlecase_net_and_short_ai_are_soft():
    """Boundary cases the surface-variant rule does NOT cover (Title-case Net->net8,
    short AI->ai2) are soft, not hard."""
    for entity, src in (("Net", "net8 improves throughput"), ("AI", "the ai2 benchmark")):
        v = _cov(f"The {entity} layer routes requests [1].", [entity], src)
        assert v.passed
        assert not v.hard_failures


def test_versioned_entity_extra_digit_suffix_is_soft():
    """Reverse-direction / digit-collision (WSL2 vs wsl22) is soft, not hard."""
    v = _cov("WSL2 provides the feature [1].", ["WSL2"], "wsl22 networking docs cover the bridge")
    assert v.passed
    assert not v.hard_failures


# --- surface-variant carve-out now only graduates WARNING TEXT ---

def test_alias_gets_specific_surface_variant_wording():
    """A known alias ("dockerd" for "Docker Engine") gets the precise
    surface-form-variant note, not the generic unverified caveat."""
    v = _cov(
        "Using the native Docker Engine avoids the vpnkit proxy [1].",
        ["Docker Engine"],
        "running dockerd directly bypasses the vpnkit layer",
    )
    assert v.passed
    assert any("surface-form variant" in w for w in v.soft_warnings)


def test_version_suffix_gets_specific_surface_variant_wording():
    """A version-suffix variant ("wsl2" for "WSL") gets the surface-form-variant note."""
    v = _cov(
        "WSL shares the host routing table when mirrored [1].",
        ["WSL"],
        "wsl2 in mirrored mode shares the host routing table",
    )
    assert v.passed
    assert any("surface-form variant" in w for w in v.soft_warnings)


def test_no_variant_gets_strong_unverified_wording():
    """A cited uncovered entity with no alias/variant gets the stronger UNVERIFIED
    caveat (distinct from the surface-variant note)."""
    v = _cov(
        "Prisma relicensed to SSPL [1].",
        ["Prisma"],
        "a source about orm tooling with no vendor named",
    )
    assert any("UNVERIFIED" in w for w in v.soft_warnings)


def test_exactly_covered_entity_not_flagged():
    """An entity covered by exact phrase is not 'uncovered' -> no warning at all."""
    v = _cov(
        "Mullvad lockdown drops anything outside the tunnel [1].",
        ["Mullvad"],
        "mullvad lockdown mode blocks all non-tunnel traffic",
    )
    assert v.passed
    assert not any("Mullvad" in w for w in v.soft_warnings)


# --- structural gates STILL hard (the demotion did not touch them) ---

def test_zero_citations_still_hard_fails():
    """cited_count==0 with sources present stays hard - the compressor produced no
    source-addressable synthesis."""
    v = verify_synthesis_output(
        content="A confident answer with no citation markers at all.",
        llm_output=None,
        cited_count=0,
        source_count=3,
        query_entities=["Prisma"],
        sources_text="orm tooling overview",
    )
    assert not v.passed
    assert any("cites none" in f for f in v.hard_failures)


def test_truncated_still_hard_fails():
    """Truncated-at-ceiling stays hard even alongside a (soft) entity-coverage caveat."""
    v = verify_synthesis_output(
        content="Prisma is SSPL [1] and the rest is cut o",
        llm_output=LLMOutput(
            text="x", source_field="content", finish_reason="length",
            truncated=True, reasoning_only=False,
        ),
        cited_count=1,
        source_count=1,
        query_entities=["Prisma"],
        sources_text="orm tooling overview",
    )
    assert not v.passed
    assert any("truncated" in f for f in v.hard_failures)


# --- the caveat rides in the annotated (and therefore cached) output ---

def test_soft_caveat_is_annotated_into_output():
    """annotate_with_verdict folds the soft entity-coverage caveat into the returned
    string (codex verified MCP caches THIS annotated output at mcp_server.py:716, so a
    cache hit carries the caveat). The output is NOT prefixed with the FAILED header."""
    v = _cov(
        "Prisma relicensed to SSPL [1].",
        ["Prisma"],
        "a source about orm tooling with no vendor named",
    )
    annotated = annotate_with_verdict("Prisma relicensed to SSPL [1].", v)
    assert "Verification notes" in annotated
    assert "UNVERIFIED" in annotated
    assert "Synthesis verification FAILED" not in annotated
