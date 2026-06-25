"""
Contradiction Detection

Research basis: PaperQA2 (arXiv:2409.13740)
- Identifies 2.34 contradictions per paper on average
- 70% of detected contradictions validated by human experts
- Key: Search for evidence SUPPORTING *and* CONTRADICTING each claim

contracrow setting from PaperQA2:
- Primary focus on contradiction detection
- Surface ALL disagreements, not just major ones
- Flag claims that need additional verification
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import (
    LLMOutput,
    ExtractionMode,
    call_with_extraction,
    derive_effective_budget,
    get_context_window,
)

# Detector input-budget sizing (D2). The per-source content slice is derived
# from the model's context window minus the reserved answer budget, the prompt
# overhead, and the query - divided across the sources and clamped - rather
# than a flat cap. A flat 1500-char cap hid relative-claim qualifiers
# ("...relative to LFM2") past the cutoff and manufactured contradictions.
_CHARS_PER_TOKEN = 4  # codebase-wide tokenization estimate (chars/4 ~= tokens)
_DETECTOR_PROMPT_OVERHEAD_TOKENS = 320  # DETECTION_PROMPT template + instructions
_DETECTOR_SOURCE_CHAR_FLOOR = 1500  # small sets get >= the old cap when affordable
_DETECTOR_SOURCE_CHAR_CEILING = 8000  # one huge source can't blow the input budget


class ContradictionSeverity(str, Enum):
    """Severity levels for contradictions."""
    MINOR = "minor"      # Different wording, same meaning
    MODERATE = "moderate"  # Different emphasis or scope
    MAJOR = "major"      # Direct factual disagreement


@dataclass
class Contradiction:
    """A detected contradiction between sources."""
    topic: str
    position_a: str
    source_a: int
    position_b: str
    source_b: int
    severity: ContradictionSeverity
    resolution_hint: str = ""  # How to reconcile if possible


@dataclass
class ContradictionReport:
    """Summary of all contradictions found."""
    contradictions: list[Contradiction]
    total_found: int
    major_count: int
    moderate_count: int
    minor_count: int
    has_major_contradictions: bool


# Severities surfaced to consumers (D1). MINOR is "different wording but same
# essential meaning" by definition (see ContradictionSeverity / the detection
# prompt) - not a real disagreement - so it is filtered out of every
# consumer-facing surface and kept only as internal diagnostics on the result.
# Filtering happens exactly once, here, so every surface (synthesis
# prompt-injection, REST/MCP contradiction blocks, the post-synthesis verifier)
# consumes the same MODERATE+MAJOR view and none can re-introduce the
# MINOR-as-contradiction false positive.
SURFACED_SEVERITIES = (ContradictionSeverity.MODERATE, ContradictionSeverity.MAJOR)


def surfaced_contradictions(contradictions: list[Contradiction]) -> list[Contradiction]:
    """The consumer-facing contradiction view: MODERATE + MAJOR only.

    Raw MINOR stays available (on ContradictionDetectionResult.contradictions)
    for diagnostics; nothing is deleted, it is just not surfaced.
    """
    return [c for c in contradictions if c.severity in SURFACED_SEVERITIES]


@dataclass
class ContradictionDetectionResult:
    """Outcome of contradiction detection over a source set.

    A bare list cannot distinguish a genuine "no contradictions found" from a
    failure to parse the detector's output, or from an exception during
    detection - callers would silently treat all three as "no contradictions".
    This result type makes the failure modes explicit so callers (and the
    post-synthesis verifier) can react to them.
    """
    contradictions: list[Contradiction]
    parse_failed: bool = False    # detector output could not be parsed
    fallback_used: bool = False   # heuristic detector was used instead of the LLM
    error: Optional[str] = None   # exception text if detection raised

    @property
    def surfaced(self) -> list[Contradiction]:
        """Canonical consumer-facing contradictions (MODERATE+MAJOR).

        Every surface reads this instead of `.contradictions` so MINOR is
        filtered exactly once. `.contradictions` remains the raw diagnostics.
        """
        return surfaced_contradictions(self.contradictions)


class ContradictionDetector:
    """
    Detect contradictions between sources.

    Usage:
        detector = ContradictionDetector(llm_client)
        result = await detector.detect(query, sources)

        for c in result.contradictions:
            if c.severity == ContradictionSeverity.MAJOR:
                # Flag in synthesis output
    """

    # PaperQA2-inspired prompt structure
    DETECTION_PROMPT = """Analyze these sources for contradictions or disagreements.

Query: {query}

Sources:
{sources}

A contradiction occurs when sources make incompatible claims about the same topic.

Types:
- MINOR: Different wording but same essential meaning
- MODERATE: Different emphasis, scope, or interpretation
- MAJOR: Direct factual disagreement - cannot both be true

For each contradiction found, format as:
TOPIC: [what they disagree about]
POSITION_A: [first position - quote or paraphrase]
SOURCE_A: [source number]
POSITION_B: [opposing position]
SOURCE_B: [source number]
SEVERITY: [minor/moderate/major]
RESOLUTION: [how to reconcile, if possible]
---

Focus on MAJOR contradictions that would affect the answer.
If no contradictions found, respond with: NO_CONTRADICTIONS"""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
    ):
        """
        Initialize detector.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for LLM calls
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model

    async def detect(
        self,
        query: str,
        sources: list,
    ) -> ContradictionDetectionResult:
        """
        Detect contradictions between sources.

        Args:
            query: Research query for context
            sources: Sources to check for disagreements

        Returns:
            ContradictionDetectionResult: the detected contradictions plus
            explicit parse_failed / fallback_used / error signals, so a caller
            can tell a genuine "none found" from a parse failure or an error.
        """
        if len(sources) < 2:
            return ContradictionDetectionResult(contradictions=[])  # Need at least 2 sources to contradict

        if not self.llm_client:
            # No LLM client - heuristic detection.
            return ContradictionDetectionResult(
                contradictions=self._detect_heuristic(query, sources),
                fallback_used=True,
            )

        try:
            prompt = self.DETECTION_PROMPT.format(
                query=query,
                sources=self._format_sources(sources, query),
            )
            # Reasoning models burn output tokens on chain-of-thought before the
            # structured blocks land in `content`; a flat 2000 starves the answer
            # → PARSE_REQUIRED rejects the truncated/reasoning-only output and
            # detect() no-ops with parse_failed=True. Derive the model-aware
            # budget (mirrors the scorer + synthesis paths); computed here at the
            # operation boundary, not inside _call_llm which stays a raw forwarder.
            budget = derive_effective_budget(2000, self.model)
            output = await self._call_llm(
                prompt, max_tokens=budget, mode=ExtractionMode.PARSE_REQUIRED
            )
        except Exception as e:
            # Transport/LLM error - fall back to the heuristic detector.
            return ContradictionDetectionResult(
                contradictions=self._detect_heuristic(query, sources),
                fallback_used=True,
                error=str(e),
            )

        response = output.text

        # PARSE_REQUIRED: an empty response is not a valid "no contradictions"
        # answer - the model never produced the structured output.
        if not response.strip():
            return ContradictionDetectionResult(contradictions=[], parse_failed=True)

        if "NO_CONTRADICTIONS" in response:
            return ContradictionDetectionResult(contradictions=[])

        contradictions = self._parse_contradictions(response)
        if not contradictions:
            # Non-empty response, no NO_CONTRADICTIONS marker, yet nothing
            # parsed - the structured format was not understood. Surface it as
            # a parse failure instead of silently reporting zero contradictions.
            return ContradictionDetectionResult(contradictions=[], parse_failed=True)

        return ContradictionDetectionResult(contradictions=contradictions)

    def _format_sources(self, sources: list, query: str = "") -> str:
        """Format sources for the detection prompt.

        Size each source's content slice from the detector's INPUT budget (D2)
        rather than a flat 1500-char cap: the model context window minus the
        reserved answer budget, the prompt overhead, and the query, divided
        across the sources, ceiling-capped (never let one source blow the input
        budget) and lifted to the old flat cap only for small sets that can
        afford it (so large sets / tiny contexts never overrun the input budget
        - C4). A relative-claim qualifier ("...relative to LFM2") past a flat
        cutoff was invisible to the detector and manufactured a contradiction;
        the larger small-set budget keeps it in view.
        """
        n = max(1, len(sources))
        context_tokens = get_context_window(self.model)
        answer_tokens = derive_effective_budget(2000, self.model)
        query_tokens = len(query) // _CHARS_PER_TOKEN
        input_tokens = (
            context_tokens - answer_tokens - _DETECTOR_PROMPT_OVERHEAD_TOKENS - query_tokens
        )
        input_chars = max(0, input_tokens) * _CHARS_PER_TOKEN
        # Per-source fair share, ceiling-capped so one source can't dominate.
        per_source_chars = min(_DETECTOR_SOURCE_CHAR_CEILING, input_chars // n)
        # Small-set generosity: lift a short source list back up to the old flat
        # cap so we never regress it - but ONLY when the whole set can afford it
        # (n * floor fits the input budget). On large sets / tiny contexts where
        # an unconditional floor would push the total past the budget, keep the
        # budget-derived share so the formatted source text never exceeds the
        # detector input budget (C4: never exceed input budget on large sets).
        if _DETECTOR_SOURCE_CHAR_FLOOR * n <= input_chars:
            per_source_chars = max(_DETECTOR_SOURCE_CHAR_FLOOR, per_source_chars)
        # No lower clamp: when the input budget cannot afford even one char per
        # source (input_chars < n), per_source_chars is 0 and the source content
        # is suppressed rather than overrunning the budget - the C4 invariant
        # ("never exceed input budget") binds over nonempty diagnostics.

        parts = []
        for i, s in enumerate(sources, 1):
            title = self._get_title(s)
            content = self._get_content(s)[:per_source_chars]
            origin = getattr(s, 'origin', 'unknown')
            parts.append(f"[{i}] {title} ({origin})\n{content}...")
        return "\n\n".join(parts)

    def _parse_contradictions(self, response: str) -> list[Contradiction]:
        """Parse contradictions from LLM response."""
        contradictions = []

        # Split by separator
        blocks = response.split("---")

        for block in blocks:
            block = block.strip()
            if not block or "TOPIC:" not in block:
                continue

            # Extract fields
            fields = {}
            for line in block.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip().upper()] = value.strip()

            try:
                severity_str = fields.get("SEVERITY", "moderate").lower()
                try:
                    severity = ContradictionSeverity(severity_str)
                except ValueError:
                    severity = ContradictionSeverity.MODERATE

                source_a = self._parse_source_num(fields.get("SOURCE_A", "1"))
                source_b = self._parse_source_num(fields.get("SOURCE_B", "2"))

                topic = fields.get("TOPIC", "").strip()
                position_a = fields.get("POSITION_A", "").strip()
                position_b = fields.get("POSITION_B", "").strip()

                # Reject blocks missing any of topic / position_a / position_b.
                # Render path would emit "- **Unknown** (moderate):  vs " — no
                # signal for the reader (codex Turn 7 v0.2.2).
                if not topic or not position_a or not position_b:
                    continue

                contradictions.append(Contradiction(
                    topic=topic,
                    position_a=position_a,
                    source_a=source_a,
                    position_b=position_b,
                    source_b=source_b,
                    severity=severity,
                    resolution_hint=fields.get("RESOLUTION", ""),
                ))
            except (ValueError, KeyError):
                continue

        return contradictions

    def _parse_source_num(self, value: str) -> int:
        """Parse source number from string."""
        try:
            # Handle "[1]" or "1" or "Source 1"
            import re
            nums = re.findall(r'\d+', value)
            return int(nums[0]) if nums else 1
        except (ValueError, IndexError):
            return 1

    def detect_sync(
        self,
        query: str,
        sources: list,
    ) -> list[Contradiction]:
        """
        Synchronous heuristic-only detection.

        For benchmarking and cases where LLM is not available.
        """
        return self._detect_heuristic(query, sources)

    def _detect_heuristic(
        self,
        query: str,
        sources: list,
    ) -> list[Contradiction]:
        """
        Heuristic contradiction detection.

        Looks for opposing language patterns between sources.
        """
        contradictions = []

        # Keywords that indicate negation or opposition
        negation_patterns = [
            ("is", "is not"),
            ("can", "cannot"),
            ("does", "does not"),
            ("should", "should not"),
            ("better", "worse"),
            ("faster", "slower"),
            ("more", "less"),
            ("increases", "decreases"),
            ("improves", "degrades"),
        ]

        for i, source_a in enumerate(sources):
            content_a = self._get_content(source_a).lower()

            for j, source_b in enumerate(sources[i+1:], i+1):
                content_b = self._get_content(source_b).lower()

                # Check for opposing patterns
                for pos, neg in negation_patterns:
                    # Very simple check - if one has positive and other has negative
                    if pos in content_a and neg in content_b:
                        contradictions.append(Contradiction(
                            topic=f"Usage of '{pos}' vs '{neg}'",
                            position_a=f"Source suggests '{pos}'",
                            source_a=i + 1,
                            position_b=f"Source suggests '{neg}'",
                            source_b=j + 1,
                            severity=ContradictionSeverity.MODERATE,
                            resolution_hint="Review both sources for context",
                        ))
                        break

        return contradictions[:5]  # Limit heuristic results

    def create_report(
        self,
        contradictions: list[Contradiction],
    ) -> ContradictionReport:
        """
        Create a summary report of contradictions.

        Args:
            contradictions: List of detected contradictions

        Returns:
            ContradictionReport with summary stats
        """
        major = sum(1 for c in contradictions if c.severity == ContradictionSeverity.MAJOR)
        moderate = sum(1 for c in contradictions if c.severity == ContradictionSeverity.MODERATE)
        minor = sum(1 for c in contradictions if c.severity == ContradictionSeverity.MINOR)

        return ContradictionReport(
            contradictions=contradictions,
            total_found=len(contradictions),
            major_count=major,
            moderate_count=moderate,
            minor_count=minor,
            has_major_contradictions=major > 0,
        )

    def format_for_synthesis(
        self,
        contradictions: list[Contradiction],
    ) -> str:
        """
        Format contradictions for inclusion in synthesis prompt.

        Returns text that can be inserted into synthesis prompt
        to ensure contradictions are surfaced in output. MINOR contradictions
        are filtered here (D1) so the synthesis prompt-injection path can never
        carry a "same meaning, different wording" false positive, regardless of
        what the caller passes.
        """
        contradictions = surfaced_contradictions(contradictions)
        if not contradictions:
            return ""

        parts = ["DETECTED CONTRADICTIONS:"]

        for i, c in enumerate(contradictions, 1):
            parts.append(f"""
{i}. {c.topic} ({c.severity.value})
   - Source [{c.source_a}]: {c.position_a}
   - Source [{c.source_b}]: {c.position_b}
   - Note: {c.resolution_hint or 'Present both perspectives'}""")

        parts.append("""
INSTRUCTION: For each contradiction above, present BOTH perspectives fairly.
Use language like "Source A suggests X, while Source B indicates Y".
Indicate which position has more support, if clear.""")

        return "\n".join(parts)

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
        max_tokens: int = 2000,
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
