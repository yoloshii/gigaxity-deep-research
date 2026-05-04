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
from ..llm_utils import get_llm_content


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


class ContradictionDetector:
    """
    Detect contradictions between sources.

    Usage:
        detector = ContradictionDetector(llm_client)
        contradictions = await detector.detect(query, sources)

        for c in contradictions:
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
    ) -> list[Contradiction]:
        """
        Detect contradictions between sources.

        Args:
            query: Research query for context
            sources: Sources to check for disagreements

        Returns:
            List of detected contradictions
        """
        if len(sources) < 2:
            return []  # Need at least 2 sources to contradict

        if not self.llm_client:
            # Fall back to heuristic detection
            return self._detect_heuristic(query, sources)

        try:
            prompt = self.DETECTION_PROMPT.format(
                query=query,
                sources=self._format_sources(sources),
            )

            response = await self._call_llm(prompt)

            if "NO_CONTRADICTIONS" in response:
                return []

            return self._parse_contradictions(response)
        except Exception:
            # On error, try heuristic
            return self._detect_heuristic(query, sources)

    def _format_sources(self, sources: list) -> str:
        """Format sources for detection prompt."""
        parts = []
        for i, s in enumerate(sources, 1):
            title = self._get_title(s)
            content = self._get_content(s)[:1500]
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

                contradictions.append(Contradiction(
                    topic=fields.get("TOPIC", "Unknown"),
                    position_a=fields.get("POSITION_A", ""),
                    source_a=source_a,
                    position_b=fields.get("POSITION_B", ""),
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
        to ensure contradictions are surfaced in output.
        """
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

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM for detection."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
        )
        return get_llm_content(response.choices[0].message)
