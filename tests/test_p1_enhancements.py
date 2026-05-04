"""Tests for P1 Enhancement modules.

P1 Enhancements:
- Synthesis: outline.py, rcs.py, presets.py
- Discovery: focus_modes.py
"""

import pytest
from src.synthesis import (
    OutlineGuidedSynthesizer,
    SynthesisOutline,
    CritiqueResult,
    OutlinedSynthesis,
    generate_outline_heuristic,
    RCSPreprocessor,
    ContextualSummary,
    RCSResult,
    SynthesisPreset,
    PresetName,
    PresetOverrides,
    get_preset,
    get_preset_by_enum,
    list_presets,
    apply_overrides,
    SynthesisStyle,
)
from src.discovery import (
    FocusModeSelector,
    FocusModeType,
    FocusMode,
    get_focus_mode,
    get_gap_categories,
    get_search_params,
    FOCUS_MODES,
)


# =============================================================================
# P1 Synthesis Tests - Outline-Guided (SciRAG)
# =============================================================================


class TestOutlineGuidedSynthesizer:
    """Tests for SciRAG-style outline-guided synthesis."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_synthesizer_initialization(self):
        """Synthesizer initializes without LLM client."""
        synthesizer = OutlineGuidedSynthesizer(llm_client=None)
        assert synthesizer is not None
        assert synthesizer.max_refinement_rounds == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_synthesizer_custom_refinement_rounds(self):
        """Synthesizer accepts custom refinement rounds."""
        synthesizer = OutlineGuidedSynthesizer(
            llm_client=None,
            max_refinement_rounds=3
        )
        assert synthesizer.max_refinement_rounds == 3


class TestHeuristicOutlineGeneration:
    """Tests for heuristic outline generation."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_comparison_query_outline(self):
        """Comparison queries get comparison-style outline."""
        outline = generate_outline_heuristic(
            "Compare FastAPI vs Flask",
            SynthesisStyle.COMPARATIVE
        )

        assert isinstance(outline, SynthesisOutline)
        assert len(outline.sections) >= 3
        section_names = [s.lower() for s in outline.sections]
        assert any("diff" in s or "vs" in s or "comparison" in s for s in section_names) or \
               "key differences" in section_names

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tutorial_query_outline(self):
        """Tutorial queries get step-by-step outline."""
        outline = generate_outline_heuristic(
            "How to implement OAuth2 in FastAPI",
            SynthesisStyle.TUTORIAL
        )

        assert isinstance(outline, SynthesisOutline)
        section_names = [s.lower() for s in outline.sections]
        assert any("step" in s or "prerequisite" in s or "guide" in s for s in section_names)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_explanation_query_outline(self):
        """Explanation queries get definitional outline."""
        outline = generate_outline_heuristic(
            "What is dependency injection",
            SynthesisStyle.COMPREHENSIVE
        )

        assert isinstance(outline, SynthesisOutline)
        section_names = [s.lower() for s in outline.sections]
        assert any("definition" in s or "concept" in s or "example" in s for s in section_names)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_academic_style_outline(self):
        """Academic style gets scholarly outline."""
        outline = generate_outline_heuristic(
            "Research on transformer attention",
            SynthesisStyle.ACADEMIC
        )

        assert isinstance(outline, SynthesisOutline)
        section_names = [s.lower() for s in outline.sections]
        # Academic outlines should have background, analysis, discussion, or conclusions
        assert any(
            "background" in s or "analysis" in s or
            "discussion" in s or "conclusion" in s
            for s in section_names
        )


# =============================================================================
# P1 Synthesis Tests - RCS (PaperQA2)
# =============================================================================


class TestRCSPreprocessor:
    """Tests for Ranking & Contextual Summarization."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_rcs_initialization(self):
        """RCS preprocessor initializes correctly."""
        rcs = RCSPreprocessor()
        assert rcs is not None
        assert rcs.min_relevance == 0.3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_rcs_custom_relevance(self):
        """RCS accepts custom relevance threshold."""
        rcs = RCSPreprocessor(min_relevance=0.5)
        assert rcs.min_relevance == 0.5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_heuristic_summarization(self, pre_gathered_sources):
        """Heuristic summarization creates contextual summaries."""
        rcs = RCSPreprocessor(min_relevance=0.0)  # Accept all
        result = rcs.prepare_sync(
            "Compare FastAPI vs Flask performance",
            pre_gathered_sources,
            top_k=5
        )

        assert isinstance(result, RCSResult)
        assert result.total_sources == len(pre_gathered_sources)
        assert result.kept_sources <= result.total_sources
        assert len(result.summaries) <= 5

        for summary in result.summaries:
            assert isinstance(summary, ContextualSummary)
            assert summary.summary
            assert 0.0 <= summary.relevance_score <= 1.0
            assert summary.source is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_relevance_filtering(self, low_quality_sources):
        """Low relevance sources are filtered out."""
        rcs = RCSPreprocessor(min_relevance=0.5)
        result = rcs.prepare_sync(
            "Python async programming tutorial",
            low_quality_sources,
            top_k=5
        )

        # All kept sources should meet minimum relevance
        for summary in result.summaries:
            assert summary.relevance_score >= 0.5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_rcs_empty_sources(self):
        """RCS handles empty source list."""
        rcs = RCSPreprocessor()
        result = rcs.prepare_sync("Any query", [], top_k=5)

        assert result.total_sources == 0
        assert result.kept_sources == 0
        assert len(result.summaries) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_rcs_top_k_limit(self, pre_gathered_sources):
        """RCS respects top_k limit."""
        rcs = RCSPreprocessor(min_relevance=0.0)
        result = rcs.prepare_sync(
            "Web frameworks",
            pre_gathered_sources,
            top_k=2
        )

        assert len(result.summaries) <= 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_key_points_extraction(self, pre_gathered_sources):
        """Heuristic summarization extracts key points."""
        rcs = RCSPreprocessor(min_relevance=0.0)
        result = rcs.prepare_sync(
            "FastAPI features",
            pre_gathered_sources,
            top_k=5
        )

        # At least some summaries should have key points
        has_key_points = any(len(s.key_points) > 0 for s in result.summaries)
        assert has_key_points or len(result.summaries) == 0


# =============================================================================
# P1 Discovery Tests - Focus Modes (Perplexica)
# =============================================================================


class TestFocusModeSelector:
    """Tests for Perplexica-style focus mode selection."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_selector_initialization(self):
        """Selector initializes without LLM."""
        selector = FocusModeSelector()
        assert selector is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_comparison_mode_detection(self):
        """Comparison queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "Compare React vs Vue",
            "FastAPI versus Flask",
            "What's the difference between Python and JavaScript",
            "Pros and cons of Redux",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.COMPARISON, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tutorial_mode_detection(self):
        """Tutorial queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "How to implement OAuth2 in FastAPI",
            "Tutorial on React hooks",
            "Getting started with Docker",
            "Learn Python for beginners",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.TUTORIAL, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_debugging_mode_detection(self):
        """Debugging queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "TypeError: Cannot read property 'map' of undefined",
            "Python exception handling error",
            "Fix npm install failed",
            "Bug in React component not rendering",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.DEBUGGING, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_academic_mode_detection(self):
        """Academic queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "Research papers on transformer attention",
            "ArXiv studies on machine learning",
            "Scientific methodology for NLP",
            "Citation analysis of deep learning papers",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.ACADEMIC, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_documentation_mode_detection(self):
        """Documentation queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "FastAPI API reference for dependencies",
            "Python documentation for asyncio",
            "Function parameters for pandas read_csv",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.DOCUMENTATION, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_news_mode_detection(self):
        """News queries detected correctly."""
        selector = FocusModeSelector()

        queries = [
            "Latest updates on Python 3.13",
            "Recently released React 19 features",
            "New version of FastAPI announced",
        ]

        for query in queries:
            mode = selector.select_sync(query)
            assert mode == FocusModeType.NEWS, f"Failed for: {query}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_general_mode_fallback(self):
        """Ambiguous queries fall back to general mode."""
        selector = FocusModeSelector()
        mode = selector.select_sync("Python")
        assert mode == FocusModeType.GENERAL


class TestFocusModeConfiguration:
    """Tests for focus mode configurations."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_modes_defined(self):
        """All focus mode types have configurations."""
        for mode_type in FocusModeType:
            mode = FOCUS_MODES.get(mode_type)
            assert mode is not None, f"Missing config for {mode_type}"
            assert isinstance(mode, FocusMode)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_mode_has_required_fields(self):
        """Each mode has required configuration fields."""
        for mode_type, mode in FOCUS_MODES.items():
            assert mode.name, f"{mode_type} missing name"
            assert mode.description, f"{mode_type} missing description"
            assert isinstance(mode.search_expansion, bool)
            assert isinstance(mode.priority_engines, list)
            assert isinstance(mode.gap_categories, list)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_focus_mode_by_name(self):
        """get_focus_mode returns correct mode."""
        mode = get_focus_mode("academic")
        assert mode.name == "Academic"

        mode = get_focus_mode("tutorial")
        assert mode.name == "Tutorial"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_focus_mode_invalid(self):
        """Invalid mode name returns general mode."""
        mode = get_focus_mode("nonexistent")
        assert mode.name == "General"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_gap_categories(self):
        """get_gap_categories returns correct categories."""
        categories = get_gap_categories(FocusModeType.DEBUGGING)
        assert "root_cause" in categories or "workarounds" in categories

        categories = get_gap_categories(FocusModeType.ACADEMIC)
        assert "methodology" in categories or "citations" in categories

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_search_params(self):
        """get_search_params returns correct parameters."""
        params = get_search_params(FocusModeType.NEWS)
        assert "expand_searches" in params
        assert "priority_engines" in params
        assert params.get("time_filter") == "week"

        params = get_search_params(FocusModeType.DOCUMENTATION)
        assert params["expand_searches"] is False


# =============================================================================
# P1 Synthesis Tests - Presets (PaperQA2)
# =============================================================================


class TestSynthesisPresets:
    """Tests for PaperQA2-style synthesis presets."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_presets_defined(self):
        """All preset names have configurations."""
        for preset_name in PresetName:
            preset = get_preset_by_enum(preset_name)
            assert preset is not None
            assert isinstance(preset, SynthesisPreset)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_preset_by_name(self):
        """get_preset returns correct preset (OpenRouter-optimized)."""
        preset = get_preset("fast")
        assert preset.name == "Fast"
        assert preset.use_outline is False
        assert preset.use_rcs is False

        preset = get_preset("tutorial")
        assert preset.name == "Tutorial"
        assert preset.use_outline is True
        assert preset.use_rcs is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_preset_invalid(self):
        """Invalid preset name falls back to DEFAULT_PRESET (comprehensive)."""
        preset = get_preset("nonexistent")
        assert preset.name == "Comprehensive"
        # `comprehensive` is a valid preset, not a fallback case.
        preset = get_preset("comprehensive")
        assert preset.name == "Comprehensive"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_list_presets(self):
        """list_presets returns the full five-preset set."""
        presets = list_presets()

        # Source defines five presets: comprehensive, fast, contracrow, academic, tutorial.
        assert len(presets) == 5
        for p in presets:
            assert "name" in p
            assert "value" in p
            assert "description" in p
            assert "style" in p
            assert "max_tokens" in p

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_presets_resolve(self):
        """Every documented preset name resolves to a real preset (not a fallback)."""
        expected = {
            "comprehensive": "Comprehensive",
            "fast": "Fast",
            "contracrow": "Contracrow",
            "academic": "Academic",
            "tutorial": "Tutorial",
        }
        for slug, display in expected.items():
            preset = get_preset(slug)
            assert preset.name == display, f"{slug} should resolve to {display}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tutorial_preset(self):
        """Tutorial preset has guide-friendly settings."""
        preset = get_preset("tutorial")
        assert preset.use_outline is True
        assert preset.style == SynthesisStyle.TUTORIAL

    @pytest.mark.unit
    @pytest.mark.p1
    def test_fast_preset_minimal(self):
        """Fast preset skips optional steps."""
        preset = get_preset("fast")
        assert preset.verify_citations is False
        assert preset.detect_contradictions is False
        assert preset.use_outline is False
        assert preset.use_rcs is False
        assert preset.run_quality_gate is False


class TestPresetOverrides:
    """Tests for preset override functionality."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_apply_single_override(self):
        """Single override applies correctly."""
        base = get_preset("tutorial")  # Use tutorial (has use_outline=True)
        overrides = PresetOverrides(max_tokens=8000)

        custom = apply_overrides(base, overrides)

        assert custom.max_tokens == 8000
        assert custom.use_outline == base.use_outline  # Unchanged
        assert custom.use_rcs == base.use_rcs  # Unchanged

    @pytest.mark.unit
    @pytest.mark.p1
    def test_apply_multiple_overrides(self):
        """Multiple overrides apply correctly."""
        base = get_preset("fast")
        overrides = PresetOverrides(
            max_tokens=2000,
            verify_citations=True,
            temperature=0.9
        )

        custom = apply_overrides(base, overrides)

        assert custom.max_tokens == 2000
        assert custom.verify_citations is True
        assert custom.temperature == 0.9
        assert custom.use_outline == base.use_outline  # Still False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_override_preserves_base(self):
        """Overrides don't modify base preset."""
        base = get_preset("fast")
        original_tokens = base.max_tokens

        overrides = PresetOverrides(max_tokens=10000)
        apply_overrides(base, overrides)

        # Base should be unchanged
        assert base.max_tokens == original_tokens


# =============================================================================
# Integration Tests (require LLM)
# =============================================================================


class TestP1SynthesisIntegration:
    """Integration tests for P1 synthesis with LLM."""

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_outline_synthesis_with_llm(self, llm_client, pre_gathered_sources):
        """Outline-guided synthesis with LLM."""
        from src.config import settings

        synthesizer = OutlineGuidedSynthesizer(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        result = await synthesizer.synthesize(
            "Compare FastAPI vs Flask for production APIs",
            pre_gathered_sources,
            style=SynthesisStyle.COMPARATIVE,
        )

        assert isinstance(result, OutlinedSynthesis)
        assert len(result.outline.sections) >= 2
        assert len(result.sections) >= 2
        assert result.content
        assert result.word_count > 0

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_rcs_with_llm(self, llm_client, pre_gathered_sources):
        """RCS contextual summarization with LLM."""
        from src.config import settings

        rcs = RCSPreprocessor(
            llm_client=llm_client,
            model=settings.llm_model,
            min_relevance=0.2,
        )

        result = await rcs.prepare(
            "FastAPI performance and features",
            pre_gathered_sources,
            top_k=3,
        )

        assert isinstance(result, RCSResult)
        assert result.kept_sources <= 3

        # LLM summaries should have more structured content
        for summary in result.summaries:
            assert summary.summary
            assert 0.0 <= summary.relevance_score <= 1.0

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_focus_mode_selection_with_llm(self, llm_client):
        """Focus mode selection with LLM for ambiguous queries."""
        from src.config import settings

        selector = FocusModeSelector(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        # Test various query types
        mode = await selector.select("FastAPI authentication patterns")
        assert mode in FocusModeType

        mode = await selector.select("Compare React and Vue ecosystem")
        assert mode == FocusModeType.COMPARISON

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    @pytest.mark.asyncio
    async def test_outline_with_critique_and_refine(
        self, llm_client, pre_gathered_sources
    ):
        """Full outline synthesis with critique and refinement."""
        from src.config import settings

        synthesizer = OutlineGuidedSynthesizer(
            llm_client=llm_client,
            model=settings.llm_model,
            max_refinement_rounds=1,
        )

        result = await synthesizer.synthesize(
            "How does FastAPI handle async requests?",
            pre_gathered_sources,
            style=SynthesisStyle.COMPREHENSIVE,
        )

        # Should have gone through critique phase
        assert result.outline is not None
        # Content should reference sources
        assert "[" in result.content or "source" in result.content.lower()
