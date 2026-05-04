"""
Focus Modes for Domain-Specific Discovery.

Research basis: Perplexica (11k+ stars) - 6 specialized focus modes
- Each mode has tailored gap templates
- Different search expansion strategies
- Priority sources per domain

Key insight: Domain-specific configurations improve search quality.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..llm_utils import get_llm_content


class FocusModeType(str, Enum):
    """Available focus modes."""
    GENERAL = "general"
    ACADEMIC = "academic"
    DOCUMENTATION = "documentation"
    COMPARISON = "comparison"
    DEBUGGING = "debugging"
    TUTORIAL = "tutorial"
    NEWS = "news"


@dataclass
class FocusMode:
    """Configuration for a focus mode."""
    name: str
    description: str
    search_expansion: bool
    priority_engines: list[str] = field(default_factory=list)
    gap_categories: list[str] = field(default_factory=list)
    metadata_boost: list[str] = field(default_factory=list)
    time_filter: Optional[str] = None  # e.g., "week", "month", "year"


# Pre-defined focus modes (Perplexica-inspired)
FOCUS_MODES: dict[FocusModeType, FocusMode] = {
    FocusModeType.GENERAL: FocusMode(
        name="General",
        description="Broad technical questions and general research",
        search_expansion=True,
        priority_engines=["google", "bing"],
        gap_categories=["documentation", "examples", "alternatives", "gotchas"],
    ),

    FocusModeType.ACADEMIC: FocusMode(
        name="Academic",
        description="Research papers, scientific studies, citations",
        search_expansion=True,
        priority_engines=["arxiv", "google scholar", "semantic scholar"],
        gap_categories=["methodology", "limitations", "replications", "critiques", "citations"],
        metadata_boost=["citation_count", "journal_impact", "recency"],
    ),

    FocusModeType.DOCUMENTATION: FocusMode(
        name="Documentation",
        description="Official docs, API references, library guides",
        search_expansion=False,  # Stay focused on official sources
        priority_engines=["google", "github"],
        gap_categories=["api_reference", "examples", "migration", "changelog", "configuration"],
    ),

    FocusModeType.COMPARISON: FocusMode(
        name="Comparison",
        description="X vs Y evaluations, choosing between options",
        search_expansion=True,
        priority_engines=["google", "stackoverflow", "reddit"],
        gap_categories=["criteria", "tradeoffs", "edge_cases", "benchmarks", "community_preference"],
    ),

    FocusModeType.DEBUGGING: FocusMode(
        name="Debugging",
        description="Error messages, bug investigation, troubleshooting",
        search_expansion=True,
        priority_engines=["stackoverflow", "github"],
        gap_categories=["error_context", "similar_issues", "root_cause", "workarounds", "fixes"],
    ),

    FocusModeType.TUTORIAL: FocusMode(
        name="Tutorial",
        description="How-to guides, step-by-step learning",
        search_expansion=False,
        priority_engines=["google", "youtube"],
        gap_categories=["prerequisites", "step_by_step", "common_mistakes", "next_steps"],
    ),

    FocusModeType.NEWS: FocusMode(
        name="News",
        description="Recent events, announcements, updates",
        search_expansion=True,
        priority_engines=["google news", "bing news"],
        gap_categories=["announcement", "reaction", "impact", "timeline"],
        time_filter="week",
    ),
}


class FocusModeSelector:
    """
    Select appropriate focus mode for a query.

    Usage:
        selector = FocusModeSelector(llm_client)
        mode = await selector.select("How to implement OAuth2 in FastAPI")
        # Returns: FocusModeType.TUTORIAL or DOCUMENTATION

        # Sync version (heuristics only):
        mode = selector.select_sync("Compare React vs Vue")
        # Returns: FocusModeType.COMPARISON
    """

    CLASSIFICATION_PROMPT = """Classify this query into one focus mode.

Query: {query}

Options:
- general: Broad technical questions
- academic: Research papers, scientific studies
- documentation: Library/framework docs, API references
- comparison: X vs Y, choosing between options
- debugging: Error messages, bug investigation
- tutorial: Learning how to do something, step-by-step guides
- news: Recent events, announcements

Respond with just the mode name (one word)."""

    # Heuristic patterns for mode detection
    MODE_PATTERNS = {
        FocusModeType.ACADEMIC: [
            "paper", "research", "study", "arxiv", "journal", "citation",
            "methodology", "experiment", "hypothesis",
        ],
        FocusModeType.COMPARISON: [
            "vs", "versus", "compare", "comparison", "better", "difference",
            "between", "which is", "pros and cons", "tradeoff",
        ],
        FocusModeType.TUTORIAL: [
            "how to", "tutorial", "guide", "learn", "step by step",
            "getting started", "beginner", "introduction to",
        ],
        FocusModeType.DEBUGGING: [
            "error", "exception", "bug", "fix", "issue", "problem",
            "not working", "failed", "crash", "traceback",
        ],
        FocusModeType.DOCUMENTATION: [
            "api", "docs", "documentation", "reference", "function",
            "method", "parameter", "configuration", "syntax",
        ],
        FocusModeType.NEWS: [
            "latest", "news", "announced", "released", "update",
            "2024", "2025", "recent", "new version",
        ],
    }

    def __init__(self, llm_client=None, model: str = None):
        """
        Initialize selector.

        Args:
            llm_client: Optional LLM for classification
            model: Model name
        """
        self.llm_client = llm_client
        self.model = model

    async def select(self, query: str) -> FocusModeType:
        """
        Select focus mode using LLM classification.

        Args:
            query: User query

        Returns:
            Appropriate FocusModeType
        """
        # Try heuristics first (fast path)
        heuristic_mode = self._classify_heuristic(query)
        if heuristic_mode != FocusModeType.GENERAL:
            return heuristic_mode

        # Use LLM for ambiguous cases
        if self.llm_client:
            return await self._classify_llm(query)

        return FocusModeType.GENERAL

    def select_sync(self, query: str) -> FocusModeType:
        """Synchronous selection using heuristics only."""
        return self._classify_heuristic(query)

    def _classify_heuristic(self, query: str) -> FocusModeType:
        """Classify query using keyword patterns."""
        query_lower = query.lower()

        # Check each mode's patterns
        scores = {}
        for mode, patterns in self.MODE_PATTERNS.items():
            score = sum(1 for p in patterns if p in query_lower)
            if score > 0:
                scores[mode] = score

        if not scores:
            return FocusModeType.GENERAL

        # Return mode with highest score
        return max(scores, key=scores.get)

    async def _classify_llm(self, query: str) -> FocusModeType:
        """Classify query using LLM."""
        prompt = self.CLASSIFICATION_PROMPT.format(query=query)

        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.1,
        )

        mode_name = get_llm_content(response.choices[0].message).strip().lower()

        try:
            return FocusModeType(mode_name)
        except ValueError:
            return FocusModeType.GENERAL

    def get_mode_config(self, mode_type: FocusModeType) -> FocusMode:
        """Get configuration for a focus mode."""
        return FOCUS_MODES.get(mode_type, FOCUS_MODES[FocusModeType.GENERAL])


def get_focus_mode(mode_name: str) -> FocusMode:
    """Get focus mode by name string."""
    try:
        mode_type = FocusModeType(mode_name.lower())
        return FOCUS_MODES[mode_type]
    except (ValueError, KeyError):
        return FOCUS_MODES[FocusModeType.GENERAL]


def get_gap_categories(mode_type: FocusModeType) -> list[str]:
    """Get gap categories for a focus mode."""
    mode = FOCUS_MODES.get(mode_type, FOCUS_MODES[FocusModeType.GENERAL])
    return mode.gap_categories


def get_search_params(mode_type: FocusModeType) -> dict:
    """Get search parameters for a focus mode."""
    mode = FOCUS_MODES.get(mode_type, FOCUS_MODES[FocusModeType.GENERAL])
    params = {
        "expand_searches": mode.search_expansion,
        "priority_engines": mode.priority_engines,
    }
    if mode.time_filter:
        params["time_filter"] = mode.time_filter
    return params
