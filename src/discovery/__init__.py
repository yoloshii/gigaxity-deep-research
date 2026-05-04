"""Discovery module for exploratory research."""

from .explorer import Explorer, DiscoveryResult, KnowledgeGap, KnowledgeLandscape, ScoredSource
from .routing import ConnectorRouter, RoutingDecision, QueryType
from .expansion import QueryExpander, ExpandedQuery
from .decomposer import QueryDecomposer, QueryAspect
from .gap_filler import GapFiller, GapFillingResult
# P1 Enhancements
from .focus_modes import (
    FocusModeType,
    FocusMode,
    FocusModeSelector,
    FOCUS_MODES,
    get_focus_mode,
    get_gap_categories,
    get_search_params,
)

__all__ = [
    # Explorer
    "Explorer",
    "DiscoveryResult",
    "KnowledgeGap",
    "KnowledgeLandscape",
    "ScoredSource",
    # Routing
    "ConnectorRouter",
    "RoutingDecision",
    "QueryType",
    # Expansion
    "QueryExpander",
    "ExpandedQuery",
    # Decomposition
    "QueryDecomposer",
    "QueryAspect",
    # Gap Filling
    "GapFiller",
    "GapFillingResult",
    # P1: Focus Modes
    "FocusModeType",
    "FocusMode",
    "FocusModeSelector",
    "FOCUS_MODES",
    "get_focus_mode",
    "get_gap_categories",
    "get_search_params",
]
