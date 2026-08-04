"""Contradiction-detector parse normalization.

`_parse_contradictions` derived each field key from the raw text left of the
first colon, so ANY decoration on the label produced a key that matched
nothing: `**TOPIC:**` -> `**TOPIC`, `1. TOPIC:` -> `1. TOPIC`, `- TOPIC:` ->
`- TOPIC`. Every field then came back empty, the topic/position guard rejected
the block, `detect()` found zero contradictions in a perfectly good response
and reported `parse_failed=True`. The live symptom was the contracrow verifier
note "contradiction detection could not be parsed" on 3 of 4 passes against a
markdown-heavy model (GLM-5.2); the prompt's format block never forbade
decoration, so the model was not misbehaving.

Scope of the claim: the parser defect below is reproduced deterministically and
is real. It is NOT established that decorated labels caused all three observed
failures — no live detector response was captured, so `finish_reason`,
`truncated` and `reasoning_only` were never ruled out for those specific runs.
Budget starvation is nonetheless an unlikely explanation for them: the live
model is `glm-5.2`, `glm` is reasoning-classified in `_REASONING_MODEL_MARKERS`,
the derived output budget is 18,384 tokens and the registered context window is
1,000,000, so the detector was not short of room.

These tests assert the CORRECT behavior: a label is a label regardless of the
markdown around it, and a response that never attempted the structured format
is reported distinctly from one whose format could not be parsed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.synthesis.contradictions import ContradictionDetector

_MODEL = "openai/gpt-4o-mini"


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


def _two_sources():
    return [_src("A", "alpha"), _src("B", "beta")]


def _detect(text):
    """Run detect() with `text` as the model's response."""
    detector = ContradictionDetector(llm_client=object(), model=_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text=text))
    detector._detect_heuristic = MagicMock()
    result = asyncio.run(detector.detect("q", _two_sources()))
    detector._detect_heuristic.assert_not_called()
    return result


def _block(topic="Autonomy claims",
           pos_a="Fully autonomous agent",
           pos_b="Requires human review",
           sev="major",
           fmt="{k}: {v}"):
    """One contradiction block with each label rendered through `fmt`."""
    rows = [
        ("TOPIC", topic),
        ("POSITION_A", pos_a),
        ("SOURCE_A", "1"),
        ("POSITION_B", pos_b),
        ("SOURCE_B", "2"),
        ("SEVERITY", sev),
        ("RESOLUTION", "launch copy overstates it"),
    ]
    return "\n".join(fmt.format(k=k, v=v) for k, v in rows) + "\n---"


# --- decorated labels must parse (the bug) --------------------------------

@pytest.mark.parametrize("fmt,label", [
    ("{k}: {v}",          "plain (the documented format)"),
    ("**{k}:** {v}",      "bold label, colon inside the emphasis"),
    ("**{k}**: {v}",      "bold label, colon outside the emphasis"),
    ("*{k}:* {v}",        "italic label"),
    ("- {k}: {v}",        "bullet list"),
    ("* {k}: {v}",        "asterisk bullet"),
    ("1. {k}: {v}",       "numbered list"),
    ("1) {k}: {v}",       "numbered list, paren"),
    ("#### {k}: {v}",     "heading"),
    ("> {k}: {v}",        "blockquote"),
    ("| {k}: | {v} |",    "table row"),
    ("- **{k}:** {v}",    "bullet + bold (compound)"),
    ("`{k}`: {v}",        "inline code label"),
    ("**`{k}`:** {v}",    "inline code inside bold"),
])
def test_decorated_labels_parse(fmt, label):
    """A label is a label regardless of the markdown around it."""
    result = _detect(_block(fmt=fmt))
    assert result.parse_failed is False, f"{label}: reported parse_failed"
    assert len(result.contradictions) == 1, f"{label}: block not parsed"
    c = result.contradictions[0]
    assert c.topic == "Autonomy claims", f"{label}: topic mangled -> {c.topic!r}"
    assert c.position_a == "Fully autonomous agent", f"{label}: position_a -> {c.position_a!r}"
    assert c.position_b == "Requires human review", f"{label}: position_b -> {c.position_b!r}"
    assert c.source_a == 1 and c.source_b == 2, f"{label}: source nums wrong"
    assert c.severity.value == "major", f"{label}: severity -> {c.severity.value}"


def test_bold_value_is_not_left_with_stray_emphasis():
    """`**TOPIC:** x` leaves a closing `**` at the head of the value."""
    result = _detect(_block(fmt="**{k}:** {v}"))
    c = result.contradictions[0]
    assert not c.topic.startswith("*")
    assert not c.position_a.startswith("*")
    assert "**" not in c.topic


def test_emphasised_value_keeps_its_text():
    """A bold VALUE keeps its words (emphasis markers may go, text may not)."""
    result = _detect(_block(topic="**Autonomy claims**"))
    assert result.contradictions[0].topic.strip("* ") == "Autonomy claims"


def test_mixed_decoration_within_one_block():
    """Models decorate inconsistently — per-line, not per-block."""
    text = (
        "**TOPIC:** Autonomy claims\n"
        "POSITION_A: Fully autonomous agent\n"
        "- SOURCE_A: 1\n"
        "*POSITION_B:* Requires human review\n"
        "2. SOURCE_B: 2\n"
        "**SEVERITY**: major\n"
        "---"
    )
    result = _detect(text)
    assert result.parse_failed is False
    assert len(result.contradictions) == 1
    c = result.contradictions[0]
    assert c.topic == "Autonomy claims"
    assert c.position_b == "Requires human review"
    assert c.severity.value == "major"


def test_multiple_decorated_blocks_all_parse():
    text = (
        _block(topic="Autonomy claims", fmt="**{k}:** {v}")
        + "\n"
        + _block(topic="User count", pos_a="20,000 users",
                 pos_b="1,400 users", sev="moderate", fmt="**{k}:** {v}")
    )
    result = _detect(text)
    assert result.parse_failed is False
    assert len(result.contradictions) == 2
    assert {c.topic for c in result.contradictions} == {"Autonomy claims", "User count"}


# --- regressions: shapes that already worked must keep working -------------

def test_preamble_before_block_still_parses():
    result = _detect("I analyzed the sources. Here is what I found:\n\n" + _block())
    assert result.parse_failed is False
    assert len(result.contradictions) == 1


def test_fenced_code_block_still_parses():
    result = _detect("```\n" + _block() + "\n```")
    assert result.parse_failed is False
    assert len(result.contradictions) == 1


def test_block_missing_required_fields_still_rejected():
    """The topic/position guard must survive normalization (codex T7 v0.2.2)."""
    text = "**TOPIC:** Autonomy claims\n**SEVERITY:** major\n---"
    result = _detect(text)
    assert result.contradictions == []
    assert result.parse_failed is True


# --- the sentinel: accept the variants models actually emit ----------------

@pytest.mark.parametrize("sentinel", [
    "NO_CONTRADICTIONS",
    "NO CONTRADICTIONS",
    "no_contradictions",
    "**NO_CONTRADICTIONS**",
    "`NO_CONTRADICTIONS`",
    "- NO_CONTRADICTIONS",
    "NO_CONTRADICTIONS.",
    "I checked every pair.\n\nNO_CONTRADICTIONS",
])
def test_no_contradictions_sentinel_variants_are_clean(sentinel):
    """Models normalize the underscore and wrap the token — all DECLARE 'none'.

    Each of these has the sentinel as a line of its own, which is what makes it
    a declaration rather than a passing mention.
    """
    result = _detect(sentinel)
    assert result.parse_failed is False, f"{sentinel!r} reported parse_failed"
    assert result.contradictions == []
    assert result.no_structured_output is False


@pytest.mark.parametrize("prose", [
    # The finding that made the matcher line-scoped: an unanchored search reads
    # this as "clean" and silently drops a real, stated disagreement.
    "There are no contradictions in the dates. However, source 1 says 10 "
    "while source 2 says 20.",
    "After review: NO CONTRADICTIONS found across the six sources.",
    "I found no contradictions worth reporting, though the two differ on scope.",
])
def test_sentinel_words_inside_prose_are_not_a_clean_result(prose):
    """Containing the words is not declaring them.

    These stay advisory (`parse_failed`) rather than being blessed as clean —
    the model never made the declaration in a form we can trust.
    """
    result = _detect(prose)
    assert result.parse_failed is True, f"{prose!r} was wrongly treated as clean"
    assert result.contradictions == []


# --- signal split: 'never attempted the format' != 'format unparseable' ----

def test_prose_answer_flags_no_structured_output():
    """A prose reply never attempted the format — distinct from a parse failure.

    Still advisory (we cannot confirm it means 'none found'), but the caller
    and the verifier can now tell the two apart instead of reporting every
    off-format reply as an unparseable one.
    """
    result = _detect("After reviewing all six sources I found no disagreements between them.")
    assert result.contradictions == []
    assert result.parse_failed is True
    assert result.no_structured_output is True


def test_markers_present_but_unparseable_is_not_no_structured_output():
    """Field markers present, nothing usable → a genuine parse failure."""
    result = _detect("TOPIC:\nPOSITION_A:\nSOURCE_A:\n---")
    assert result.contradictions == []
    assert result.parse_failed is True
    assert result.no_structured_output is False


def test_successful_parse_leaves_no_structured_output_false():
    result = _detect(_block())
    assert result.no_structured_output is False
    assert result.ambiguous_output is False


# --- self-contradictory responses: findings AND a 'none' declaration -------

def test_blocks_plus_sentinel_keeps_findings_and_flags_ambiguity():
    """A model that reports a finding then retracts it disagrees with itself.

    Neither signal may override the other silently: the finding is kept (losing
    it discards real information) and the response is marked ambiguous so the
    verifier can say the count is unconfirmed.
    """
    result = _detect(_block() + "\n\nNO_CONTRADICTIONS")
    assert len(result.contradictions) == 1
    assert result.ambiguous_output is True
    assert result.parse_failed is False


def test_clean_sentinel_alone_is_not_ambiguous():
    result = _detect("NO_CONTRADICTIONS")
    assert result.ambiguous_output is False


@pytest.mark.parametrize("topic", [
    "[API availability]",
    "[RFC 9110]",
    "[2026] pricing",
    "whether [the SDK] is required",
    # Another FIELD's placeholder is not TOPIC's placeholder — pooling them
    # into one set drops these as echoes.
    "[source number]",
    "[minor/moderate/major]",
    "[opposing position]",
])
def test_bracketed_topics_are_real_topics(topic):
    """Only TOPIC's own placeholder text is an echo.

    Models routinely keep the template's brackets and replace the contents, so
    a broad `^\\[.*\\]$` guard silently drops genuine findings — and matching
    against every field's placeholder drops a further three.
    """
    result = _detect(_block(topic=topic))
    assert result.parse_failed is False, f"{topic!r} was dropped as a placeholder"
    assert len(result.contradictions) == 1
    assert result.contradictions[0].topic == topic


def test_template_placeholders_match_the_prompt():
    """The echo guard is only correct while it mirrors the prompt verbatim.

    Asserts the field->placeholder PAIRING, not merely that the text occurs
    somewhere: a reworded prompt must fail this rather than silently disarm
    the guard.
    """
    from src.synthesis.contradictions import _TEMPLATE_PLACEHOLDERS
    prompt = ContradictionDetector.DETECTION_PROMPT
    for field, placeholder in _TEMPLATE_PLACEHOLDERS.items():
        assert f"{field}: {placeholder}" in prompt, (
            f"prompt no longer pairs {field} with {placeholder!r}"
        )


def test_echoed_template_placeholder_is_not_a_finding():
    """The model echoing the prompt's own format block is not a contradiction.

    Surfacing `[what they disagree about]` as a real topic is a false positive;
    with the sentinel alongside it, the old blocks-first order did exactly that.
    """
    echoed = (
        "TOPIC: [what they disagree about]\n"
        "POSITION_A: [first position - quote or paraphrase]\n"
        "SOURCE_A: [source number]\n"
        "POSITION_B: [opposing position]\n"
        "SOURCE_B: [source number]\n"
        "SEVERITY: [minor/moderate/major]\n"
        "---\n"
        "NO_CONTRADICTIONS"
    )
    result = _detect(echoed)
    assert result.contradictions == []
    assert result.parse_failed is False  # the sentinel is a real declaration
    assert result.ambiguous_output is False


def test_empty_response_flags_no_structured_output():
    """An empty response produced no format at all."""
    result = _detect("")
    assert result.parse_failed is True
    assert result.no_structured_output is True


# --- the normalizer itself -------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("TOPIC", "TOPIC"),
    ("  TOPIC  ", "TOPIC"),
    ("**TOPIC", "TOPIC"),
    ("*TOPIC", "TOPIC"),
    ("- TOPIC", "TOPIC"),
    ("+ TOPIC", "TOPIC"),
    ("1. TOPIC", "TOPIC"),
    ("12) TOPIC", "TOPIC"),
    ("#### TOPIC", "TOPIC"),
    ("> TOPIC", "TOPIC"),
    ("| TOPIC", "TOPIC"),
    ("- **TOPIC**", "TOPIC"),
    ("`TOPIC`", "TOPIC"),
    ("**`TOPIC`**", "TOPIC"),
    ("topic", "TOPIC"),
    ("POSITION_A", "POSITION_A"),
    ("**POSITION_A**", "POSITION_A"),
    ("- SOURCE_B", "SOURCE_B"),
])
def test_normalize_field_key(raw, expected):
    from src.synthesis.contradictions import _normalize_field_key
    assert _normalize_field_key(raw) == expected


def test_normalize_field_key_preserves_internal_underscores():
    """POSITION_A must not lose its underscore to emphasis stripping."""
    from src.synthesis.contradictions import _normalize_field_key
    assert _normalize_field_key("_POSITION_A_") == "POSITION_A"


# --- the verifier branches these flags drive ------------------------------
# The detector flags are only worth setting if the user-facing warning follows
# them. Without these, an inverted branch or a wiring regression leaves every
# detector-level test above green (codex T1 Low).

def _verify(detection):
    from src.synthesis.output_verifier import verify_synthesis_output
    return verify_synthesis_output(
        content="A synthesis body citing [1].",
        llm_output=None,
        cited_count=1,
        source_count=2,
        contradiction_result=detection,
    )


def _warnings(detection):
    return " | ".join(_verify(detection).soft_warnings)


def test_verifier_reports_unparseable_distinctly_from_off_format():
    """Format attempted but unparseable → the grammar-failure wording."""
    detection = _detect("TOPIC:\nPOSITION_A:\nSOURCE_A:\n---")
    assert detection.parse_failed and not detection.no_structured_output
    text = _warnings(detection)
    assert "could not be parsed" in text
    assert "no structured output" not in text


def test_verifier_reports_off_format_distinctly_from_unparseable():
    """No format attempted → the off-format wording, not 'could not be parsed'."""
    detection = _detect("I reviewed the sources and have nothing structured to add.")
    assert detection.parse_failed and detection.no_structured_output
    text = _warnings(detection)
    assert "no structured output" in text
    assert "could not be parsed" not in text


def test_verifier_flags_self_contradictory_detector_output():
    """The ambiguity warning rides ALONGSIDE the finding-count warning.

    Asserting only the ambiguity string would still pass if the two branches
    were made mutually exclusive, which would hide the retained findings from
    the reader (codex T2 Low).
    """
    detection = _detect(_block() + "\n\nNO_CONTRADICTIONS")
    assert detection.ambiguous_output is True
    text = _warnings(detection)
    assert "contradiction(s) detected" in text
    assert "self-contradictory" in text


def test_verifier_silent_on_a_clean_detection():
    """A clean run must not manufacture any contradiction warning."""
    detection = _detect("NO_CONTRADICTIONS")
    text = _warnings(detection)
    assert "contradiction detection" not in text
    assert "self-contradictory" not in text
