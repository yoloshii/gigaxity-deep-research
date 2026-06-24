"""Verifier: under the entity-coverage demotion (codex DESIGN 019e5b0f), the
ALL-CAPS framing carve-out no longer changes pass/fail - everything in
entity-coverage is a soft warning now. The carve-out survives ONLY to graduate
the warning TEXT: shouted framing whose every space/hyphen part is a common word
("MEASUREMENT PLANE", "CONTROL PLANE", "NET-NEW", "NET") gets the
"emphasis/framing" note, while a real acronym ("AWS"/"GCP"/"HIPAA") or a
Title-case entity ("Tavily") falls through to the stronger "treat as unverified"
entity-coverage caveat. All pass; none hard-fail.
"""
from src.synthesis.output_verifier import verify_synthesis_output


def _v(content, query_entities, sources_text):
    return verify_synthesis_output(
        content=content,
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=query_entities,
        sources_text=sources_text,
    )


def test_framing_phrase_gets_emphasis_note_and_passes():
    v = _v(
        "The MEASUREMENT PLANE coordinates the run and records receipts [1].",
        ["MEASUREMENT PLANE"],
        "a source about coordinating runs and recording receipts",
    )
    assert v.passed and not v.hard_failures
    assert any("emphasis/framing" in w for w in v.soft_warnings)


def test_multiple_framing_tokens_pass_as_emphasis():
    v = _v(
        "The NET-NEW contracts are NET additions, not NEW rewrites [1].",
        ["NET-NEW", "NET", "NEW"],
        "a source discussing contracts and additions only",
    )
    assert v.passed and not v.hard_failures
    assert any("emphasis/framing" in w for w in v.soft_warnings)


def test_control_plane_phrase_is_emphasis():
    v = _v(
        "The CONTROL PLANE dispatches work items [1].",
        ["CONTROL PLANE"],
        "a source about dispatching without that exact phrase",
    )
    assert v.passed and not v.hard_failures
    assert any("emphasis/framing" in w for w in v.soft_warnings)


def test_titlecase_entity_cited_uncovered_is_soft_unverified():
    """Formerly a HARD fail; now a soft 'treat as unverified' caveat. A Title-case
    entity (Tavily) is NOT framing, so it gets the strong caveat, not the emphasis
    note - and the synthesis still passes."""
    v = _v(
        "Tavily returns the freshest index [1].",
        ["Tavily"],
        "a source about search latency with no vendor named",
    )
    assert v.passed and not v.hard_failures
    assert any("Tavily" in w and "UNVERIFIED" in w for w in v.soft_warnings)


def test_real_acronym_cited_uncovered_is_soft_unverified():
    """Formerly HARD; now soft. Real acronyms (GCP/HIPAA) get the strong caveat, not
    the emphasis note (AWS is covered here, so only GCP/HIPAA are uncovered)."""
    v = _v(
        "AWS and GCP both provide HIPAA-ready BAAs [1].",
        ["AWS", "GCP", "HIPAA"],
        "aws documentation describes its compliance program",
    )
    assert v.passed and not v.hard_failures
    assert any(("GCP" in w or "HIPAA" in w) and "UNVERIFIED" in w for w in v.soft_warnings)


def test_uppercase_token_covered_by_source_not_flagged():
    """An ALL-CAPS token the sources DO cover is not discussed-uncovered, so it is
    neither warned nor failed."""
    v = _v(
        "The MEASUREMENT PLANE scores runs [1].",
        ["MEASUREMENT PLANE"],
        "docs describe a measurement plane that scores runs",
    )
    assert v.passed
    assert not any("MEASUREMENT PLANE" in w for w in v.soft_warnings)
