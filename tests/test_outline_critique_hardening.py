"""Outline + critique hardening (lenient-parsed-callsites design, rev 7).

The measured defect: glm-5.2 under a flat 300-token outline budget returns
finish_reason=length with 0 content chars and 1194 reasoning chars; LENIENT
extraction then handed 8 chain-of-thought fragments back as section
headings. The repair is three-layer per stage: reasoning-aware budget,
PARSE_REQUIRED extraction, and an explicit validated parse result with a
named failure state — PARSE_REQUIRED alone is not validation (any
non-truncated string passes through it unchanged).

Critique policy regression guard: a blanked critique must NOT silently skip
the refine pass (blank → issues=[] → refine skipped was the regression an
earlier design revision would have shipped). Instead: parse_failed=True and
exactly one general refinement with a synthetic issue, honouring an
explicit max_refinement_rounds == 0.
"""

from types import SimpleNamespace

import pytest

from src.degradation import DegradationCode
from src.llm_utils import derive_effective_budget
from src.synthesis.aggregator import PreGatheredSource, SynthesisStyle
from src.synthesis.outline import (
    OUTLINE_MAX_HEADING_CHARS,
    SYNTHETIC_CRITIQUE_ISSUE,
    OutlineGuidedSynthesizer,
    generate_outline_heuristic,
    parse_critique_records,
    parse_outline_records,
)


# ---------------------------------------------------------------------------
# Grammar unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutlineGrammar:
    def test_valid_records_parse(self):
        text = (
            "SECTION: Overview\n"
            "SECTION: Key Differences\n"
            "SECTION: Recommendations"
        )
        assert parse_outline_records(text) == [
            "Overview",
            "Key Differences",
            "Recommendations",
        ]

    def test_six_records_accepted(self):
        text = "\n".join(f"SECTION: Part {i}" for i in range(1, 7))
        assert len(parse_outline_records(text)) == 6

    def test_too_few_records_rejected(self):
        assert parse_outline_records("SECTION: A\nSECTION: B") is None

    def test_too_many_records_rejected(self):
        text = "\n".join(f"SECTION: Part {i}" for i in range(1, 8))
        assert parse_outline_records(text) is None

    def test_non_section_line_rejects_whole_response(self):
        text = (
            "Here is the outline:\n"
            "SECTION: Overview\n"
            "SECTION: Details\n"
            "SECTION: Summary"
        )
        assert parse_outline_records(text) is None

    def test_chain_of_thought_fragments_rejected(self):
        """The measured live failure shape: CoT fragments split into lines
        must not pass as headings (they did under the old 2-8-line check)."""
        text = (
            "1.  **Analyze the Request:**\n"
            "*   Query: compare X and Y\n"
            "*   The user wants a comparison\n"
            "2.  **Plan the sections:**"
        )
        assert parse_outline_records(text) is None

    def test_duplicate_headings_rejected(self):
        text = "SECTION: Overview\nSECTION: Details\nSECTION: overview"
        assert parse_outline_records(text) is None

    def test_empty_payload_rejected(self):
        text = "SECTION: Overview\nSECTION:\nSECTION: Details"
        assert parse_outline_records(text) is None

    def test_overlong_payload_rejected(self):
        long_heading = "H" * (OUTLINE_MAX_HEADING_CHARS + 1)
        text = f"SECTION: Overview\nSECTION: {long_heading}\nSECTION: Details"
        assert parse_outline_records(text) is None

    def test_blank_rejected(self):
        """An outline is never legitimately empty."""
        assert parse_outline_records("") is None
        assert parse_outline_records("   \n  ") is None


@pytest.mark.unit
class TestCritiqueGrammar:
    def test_no_issues_exact(self):
        assert parse_critique_records("NO_ISSUES") == []

    def test_no_issues_normalized(self):
        assert parse_critique_records('  "NO_ISSUES."  ') == []
        assert parse_critique_records("no_issues") == []

    def test_no_issues_substring_no_longer_passes(self):
        """Substring matching let NO_ISSUES inside chatter count as a clean
        verdict; whole-response matching rejects it."""
        assert (
            parse_critique_records(
                "I checked carefully and found NO_ISSUES worth mentioning"
            )
            is None
        )

    def test_issue_records_parse(self):
        text = "ISSUE: Missing citation in section 2\nISSUE: Unclear conclusion"
        assert parse_critique_records(text) == [
            "Missing citation in section 2",
            "Unclear conclusion",
        ]

    def test_mixed_non_issue_line_rejected(self):
        text = "Here are the problems:\nISSUE: Missing citation"
        assert parse_critique_records(text) is None

    def test_empty_issue_payload_rejected(self):
        assert parse_critique_records("ISSUE: real problem\nISSUE:") is None

    def test_blank_rejected(self):
        """A blank response is not a valid 'no issues' answer."""
        assert parse_critique_records("") is None


# ---------------------------------------------------------------------------
# End-to-end synthesize() harness with a routing mock client
# ---------------------------------------------------------------------------


VALID_OUTLINE = "SECTION: Overview\nSECTION: Details\nSECTION: Summary"
REASONING_TRACE = "1. **Analyze the request** the user wants a comparison..."


def _choice(content=None, reasoning_content=None, finish_reason="stop"):
    return SimpleNamespace(
        message=SimpleNamespace(
            content=content, reasoning_content=reasoning_content
        ),
        finish_reason=finish_reason,
    )


class RoutingLLMClient:
    """Dispatches canned responses by prompt stage; records every call."""

    def __init__(self, outline_choice=None, critique_choice=None):
        self.outline_choice = outline_choice or _choice(content=VALID_OUTLINE)
        self.critique_choice = critique_choice or _choice(content="NO_ISSUES")
        self.calls: list[dict] = []
        self.refine_prompts: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.calls.append(kwargs)
        if "Create an outline" in prompt:
            choice = self.outline_choice
        elif "Critique this draft" in prompt:
            choice = self.critique_choice
        elif "Refine this synthesis" in prompt:
            self.refine_prompts.append(prompt)
            choice = _choice(content="Refined synthesis text with fixes applied.")
        else:  # section fill
            choice = _choice(content="Section content with a claim [1].")
        return SimpleNamespace(choices=[choice])


@pytest.fixture
def sources():
    return [
        PreGatheredSource(
            origin="jina",
            url="https://example.com/a",
            title="Source A",
            content="Alpha content about the topic.",
            source_type="article",
        )
    ]


def _synthesizer(client, **kwargs):
    return OutlineGuidedSynthesizer(client, model="test-model", **kwargs)


@pytest.mark.unit
class TestOutlineStage:
    async def test_valid_outline_parsed_no_degradation(self, sources):
        client = RoutingLLMClient()
        result = await _synthesizer(client).synthesize("query", sources)
        assert result.outline.sections == ["Overview", "Details", "Summary"]
        assert result.degradations == []

    async def test_reasoning_only_outline_falls_back_to_heuristic(self, sources):
        """The measured failure: content empty, CoT in reasoning_content,
        finish_reason=length → heuristic outline + truncated-primary
        degradation with reasoning_only kept as the secondary flag."""
        client = RoutingLLMClient(
            outline_choice=_choice(
                content=None,
                reasoning_content=REASONING_TRACE,
                finish_reason="length",
            )
        )
        result = await _synthesizer(client).synthesize(
            "compare X vs Y", sources
        )
        expected = generate_outline_heuristic(
            "compare X vs Y", SynthesisStyle.COMPREHENSIVE
        )
        assert result.outline.sections == expected.sections
        # No CoT fragment ever becomes a heading.
        assert all("**" not in s for s in result.outline.sections)
        deg = next(d for d in result.degradations if d.stage == "outline")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is True
        assert deg.parse_failed is True
        assert deg.fallback_used is True

    async def test_truncated_nonempty_content_falls_back(self, sources):
        """Distinct path: content present but finish_reason=length —
        PARSE_REQUIRED blanks it (reasoning_only=False, source_field=content)
        and the stage must still fall back with code=truncated."""
        client = RoutingLLMClient(
            outline_choice=_choice(
                content="SECTION: Overview\nSECTION: Det",
                finish_reason="length",
            )
        )
        result = await _synthesizer(client).synthesize("query", sources)
        deg = next(d for d in result.degradations if d.stage == "outline")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is False

    async def test_wrong_shape_content_falls_back_as_malformed(self, sources):
        """Plausible-but-wrong-shape: plain headings without SECTION:
        records parse under the old count-only check, not under the grammar."""
        client = RoutingLLMClient(
            outline_choice=_choice(content="Overview\nDetails\nSummary")
        )
        result = await _synthesizer(client).synthesize("query", sources)
        deg = next(d for d in result.degradations if d.stage == "outline")
        assert deg.code is DegradationCode.MALFORMED
        assert result.outline.sections  # heuristic outline still present

    async def test_outline_budget_is_reasoning_aware(self, sources):
        """The outline call requests derive_effective_budget(300, model),
        not the flat 300 that starved the measured model."""
        client = RoutingLLMClient()
        synth = OutlineGuidedSynthesizer(client, model="glm-5.2")
        await synth.synthesize("query", sources)
        outline_call = next(
            c
            for c in client.calls
            if "Create an outline" in c["messages"][0]["content"]
        )
        expected = derive_effective_budget(300, "glm-5.2")
        assert outline_call["max_tokens"] == expected
        assert expected > 300


@pytest.mark.unit
class TestCritiqueFailurePolicy:
    async def test_blank_critique_triggers_synthetic_refinement(self, sources):
        """Anti-regression: a blanked critique must run ONE general
        refinement with the synthetic issue — not skip refine via
        issues=[]."""
        client = RoutingLLMClient(
            critique_choice=_choice(
                content=None,
                reasoning_content="thinking about issues...",
                finish_reason="length",
            )
        )
        result = await _synthesizer(client).synthesize("query", sources)
        assert len(client.refine_prompts) == 1
        assert SYNTHETIC_CRITIQUE_ISSUE in client.refine_prompts[0]
        assert result.refined is True
        deg = next(d for d in result.degradations if d.stage == "critique")
        assert deg.parse_failed is True
        assert "general refinement pass substituted" in deg.message

    async def test_rounds_zero_preserves_draft_on_critique_failure(self, sources):
        """max_refinement_rounds == 0 is an explicit caller configuration:
        no refinement even on critique failure; draft preserved; the
        degradation reports it."""
        client = RoutingLLMClient(critique_choice=_choice(content=""))
        result = await _synthesizer(
            client, max_refinement_rounds=0
        ).synthesize("query", sources)
        assert client.refine_prompts == []
        assert result.refined is False
        deg = next(d for d in result.degradations if d.stage == "critique")
        assert "draft preserved unrefined" in deg.message

    async def test_clean_no_issues_verdict_no_refine_no_degradation(self, sources):
        client = RoutingLLMClient(critique_choice=_choice(content="NO_ISSUES"))
        result = await _synthesizer(client).synthesize("query", sources)
        assert client.refine_prompts == []
        assert result.degradations == []
        assert result.critique is None

    async def test_parsed_issues_refine_normally(self, sources):
        client = RoutingLLMClient(
            critique_choice=_choice(
                content="ISSUE: Missing citation in the Details section"
            )
        )
        result = await _synthesizer(client).synthesize("query", sources)
        assert len(client.refine_prompts) == 1
        assert "Missing citation" in client.refine_prompts[0]
        assert SYNTHETIC_CRITIQUE_ISSUE not in client.refine_prompts[0]
        assert result.refined is True
        assert result.degradations == []

    async def test_truncated_nonempty_critique_takes_synthetic_refinement(
        self, sources
    ):
        """Content present but finish_reason=length: PARSE_REQUIRED blanks
        it (reasoning_only=False) — classify truncated, refine synthetically."""
        client = RoutingLLMClient(
            critique_choice=_choice(
                content="ISSUE: Missing cit", finish_reason="length"
            )
        )
        result = await _synthesizer(client).synthesize("query", sources)
        assert len(client.refine_prompts) == 1
        assert SYNTHETIC_CRITIQUE_ISSUE in client.refine_prompts[0]
        deg = next(d for d in result.degradations if d.stage == "critique")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is False

    async def test_wrong_shape_critique_takes_synthetic_refinement(self, sources):
        """Prose critique (no ISSUE: records) is malformed — never a silent
        issues=[] skip."""
        client = RoutingLLMClient(
            critique_choice=_choice(
                content="Overall the draft looks weak in a few places."
            )
        )
        result = await _synthesizer(client).synthesize("query", sources)
        assert len(client.refine_prompts) == 1
        assert SYNTHETIC_CRITIQUE_ISSUE in client.refine_prompts[0]
        deg = next(d for d in result.degradations if d.stage == "critique")
        assert deg.code is DegradationCode.MALFORMED
