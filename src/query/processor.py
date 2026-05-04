"""
Query Intelligence Module

Transforms raw user queries into enriched, actionable search intents.
This addresses Gap #1: No Query Intelligence.

Key capabilities:
1. Intent classification (factual, exploratory, comparative, tutorial, current_events)
2. Query expansion (synonyms, related terms)
3. Temporal awareness (recent, historical, specific dates)
4. Entity extraction
5. Sub-query decomposition for complex queries
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config import settings


class QueryIntent(str, Enum):
    """Classification of query intent."""
    FACTUAL = "factual"           # Single fact lookup
    EXPLORATORY = "exploratory"    # Open-ended research
    COMPARATIVE = "comparative"    # X vs Y analysis
    TUTORIAL = "tutorial"          # How-to guides
    CURRENT_EVENTS = "current_events"  # Recent news/developments
    ACADEMIC = "academic"          # Research papers/studies


class TemporalFocus(str, Enum):
    """Temporal focus of the query."""
    RECENT = "recent"       # Last few months
    CURRENT = "current"     # Right now
    HISTORICAL = "historical"  # Past events
    SPECIFIC = "specific"   # Specific date/range
    NONE = "none"           # No temporal component


@dataclass
class ProcessedQuery:
    """Enriched query with extracted intelligence."""
    original: str
    normalized: str
    intent: QueryIntent
    temporal: TemporalFocus
    temporal_hint: Optional[str] = None  # e.g., "2024", "last week"
    entities: list[str] = field(default_factory=list)
    expansions: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)

    # Connector hints based on query analysis
    suggested_connectors: list[str] = field(default_factory=list)

    # Search parameters
    freshness_weight: float = 0.1
    depth_hint: str = "medium"  # brief, medium, comprehensive


class QueryProcessor:
    """
    Processes raw queries into enriched search intents.

    Uses heuristics for fast processing, with optional LLM enhancement
    for complex queries.
    """

    # Intent classification patterns
    COMPARATIVE_PATTERNS = [
        r'\bvs\.?\b', r'\bversus\b', r'\bcompare\b', r'\bcomparison\b',
        r'\bdifference\s+between\b', r'\bbetter\b', r'\bworse\b',
        r'\badvantages?\b', r'\bdisadvantages?\b', r'\bpros?\s+and\s+cons?\b'
    ]

    TUTORIAL_PATTERNS = [
        r'^how\s+(do|to|can|should)\b', r'\bstep[- ]by[- ]step\b',
        r'\btutorial\b', r'\bguide\b', r'\bexample\b', r'\bimplement\b',
        r'\bset\s*up\b', r'\bconfigure\b', r'\binstall\b'
    ]

    FACTUAL_PATTERNS = [
        r'^what\s+is\s+the\b', r'^who\s+is\b', r'^when\s+did\b',
        r'^where\s+is\b', r'^how\s+many\b', r'^how\s+much\b',
        r'\bdefinition\b', r'\bmeaning\b'
    ]

    ACADEMIC_PATTERNS = [
        r'\bresearch\b', r'\bstudy\b', r'\bpaper\b', r'\bjournal\b',
        r'\bpublication\b', r'\barxiv\b', r'\bpubmed\b', r'\bscientific\b',
        r'\bempirical\b', r'\bexperiment\b', r'\bfindings\b'
    ]

    CURRENT_EVENTS_PATTERNS = [
        r'\blatest\b', r'\brecent\b', r'\bnew\b', r'\b2024\b', r'\b2025\b',
        r'\btoday\b', r'\bthis\s+week\b', r'\bthis\s+month\b',
        r'\bnews\b', r'\bannouncement\b', r'\bupdate\b', r'\bdevelopment\b'
    ]

    # Temporal extraction patterns
    TEMPORAL_RECENT = [r'\blatest\b', r'\brecent\b', r'\bnew\b', r'\bmodern\b']
    TEMPORAL_CURRENT = [r'\bcurrent\b', r'\bnow\b', r'\btoday\b', r'\bpresent\b']
    TEMPORAL_YEARS = r'\b(19|20)\d{2}\b'

    # Domain-specific entity patterns
    TECH_ENTITIES = [
        r'\b(Python|JavaScript|TypeScript|Rust|Go|Java|C\+\+)\b',
        r'\b(React|Vue|Angular|FastAPI|Django|Flask|Express)\b',
        r'\b(PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch)\b',
        r'\b(Docker|Kubernetes|AWS|GCP|Azure)\b',
        r'\b(GPT|Claude|LLaMA|transformer|BERT|embedding)\b',
    ]

    def __init__(self, llm_client=None):
        """
        Initialize the query processor.

        Args:
            llm_client: Optional LLM client for enhanced processing
        """
        self.llm_client = llm_client
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for efficiency."""
        self.comparative_re = [re.compile(p, re.I) for p in self.COMPARATIVE_PATTERNS]
        self.tutorial_re = [re.compile(p, re.I) for p in self.TUTORIAL_PATTERNS]
        self.factual_re = [re.compile(p, re.I) for p in self.FACTUAL_PATTERNS]
        self.academic_re = [re.compile(p, re.I) for p in self.ACADEMIC_PATTERNS]
        self.current_re = [re.compile(p, re.I) for p in self.CURRENT_EVENTS_PATTERNS]
        self.tech_entity_re = [re.compile(p, re.I) for p in self.TECH_ENTITIES]
        self.year_re = re.compile(self.TEMPORAL_YEARS)

    def process(self, query: str) -> ProcessedQuery:
        """
        Process a raw query into an enriched ProcessedQuery.

        Args:
            query: Raw user query string

        Returns:
            ProcessedQuery with extracted intelligence
        """
        normalized = self._normalize(query)
        intent = self._classify_intent(normalized)
        temporal, temporal_hint = self._extract_temporal(normalized)
        entities = self._extract_entities(normalized)
        expansions = self._expand_query(normalized, entities)
        sub_queries = self._decompose_if_complex(normalized, intent)
        connectors = self._suggest_connectors(intent, entities, temporal)
        freshness_weight = self._compute_freshness_weight(temporal, intent)
        depth = self._determine_depth(intent, normalized)

        return ProcessedQuery(
            original=query,
            normalized=normalized,
            intent=intent,
            temporal=temporal,
            temporal_hint=temporal_hint,
            entities=entities,
            expansions=expansions,
            sub_queries=sub_queries,
            suggested_connectors=connectors,
            freshness_weight=freshness_weight,
            depth_hint=depth,
        )

    def _normalize(self, query: str) -> str:
        """Normalize query text."""
        # Strip whitespace, normalize spaces
        normalized = ' '.join(query.split())
        return normalized

    def _classify_intent(self, query: str) -> QueryIntent:
        """Classify the primary intent of the query."""
        # Check patterns in order of specificity
        if any(p.search(query) for p in self.comparative_re):
            return QueryIntent.COMPARATIVE
        if any(p.search(query) for p in self.tutorial_re):
            return QueryIntent.TUTORIAL
        if any(p.search(query) for p in self.academic_re):
            return QueryIntent.ACADEMIC
        if any(p.search(query) for p in self.current_re):
            return QueryIntent.CURRENT_EVENTS
        if any(p.search(query) for p in self.factual_re):
            return QueryIntent.FACTUAL

        # Default to exploratory for open-ended queries
        return QueryIntent.EXPLORATORY

    def _extract_temporal(self, query: str) -> tuple[TemporalFocus, Optional[str]]:
        """Extract temporal focus from query."""
        # Check for specific years
        year_match = self.year_re.search(query)
        if year_match:
            return TemporalFocus.SPECIFIC, year_match.group()

        # Check for recent/current indicators
        query_lower = query.lower()
        if any(re.search(p, query_lower) for p in self.TEMPORAL_RECENT):
            return TemporalFocus.RECENT, "recent"
        if any(re.search(p, query_lower) for p in self.TEMPORAL_CURRENT):
            return TemporalFocus.CURRENT, "current"

        return TemporalFocus.NONE, None

    def _extract_entities(self, query: str) -> list[str]:
        """Extract named entities from query."""
        entities = []

        # Tech entities
        for pattern in self.tech_entity_re:
            matches = pattern.findall(query)
            entities.extend(matches)

        # Deduplicate while preserving order
        seen = set()
        unique_entities = []
        for e in entities:
            if e.lower() not in seen:
                seen.add(e.lower())
                unique_entities.append(e)

        return unique_entities

    def _expand_query(self, query: str, entities: list[str]) -> list[str]:
        """Generate query expansions for broader search coverage."""
        expansions = []

        # Add common synonyms/related terms based on entities
        expansion_map = {
            'fastapi': ['starlette', 'async python api'],
            'postgresql': ['postgres', 'psql'],
            'kubernetes': ['k8s', 'container orchestration'],
            'docker': ['container', 'containerization'],
            'react': ['reactjs', 'react.js'],
            'machine learning': ['ML', 'AI'],
            'gpt': ['large language model', 'LLM'],
            'transformer': ['attention mechanism', 'neural network'],
        }

        for entity in entities:
            entity_lower = entity.lower()
            if entity_lower in expansion_map:
                expansions.extend(expansion_map[entity_lower])

        return expansions

    def _decompose_if_complex(self, query: str, intent: QueryIntent) -> list[str]:
        """
        Decompose complex queries into sub-queries.

        Complex queries often benefit from multiple targeted searches
        that are then synthesized together.
        """
        sub_queries = [query]  # Always include original

        # Comparative queries can be split
        if intent == QueryIntent.COMPARATIVE:
            # Try to extract the items being compared
            vs_match = re.search(r'(.+?)\s+(?:vs\.?|versus|compared?\s+to)\s+(.+)', query, re.I)
            if vs_match:
                item_a, item_b = vs_match.groups()
                sub_queries.extend([
                    f"{item_a.strip()} features advantages",
                    f"{item_b.strip()} features advantages",
                    f"{item_a.strip()} {item_b.strip()} comparison benchmark"
                ])

        # Academic queries can search for papers + explanations
        elif intent == QueryIntent.ACADEMIC:
            sub_queries.append(f"{query} arxiv papers")
            sub_queries.append(f"{query} explained tutorial")

        return sub_queries

    def _suggest_connectors(
        self,
        intent: QueryIntent,
        entities: list[str],
        temporal: TemporalFocus
    ) -> list[str]:
        """Suggest which connectors to prioritize based on query analysis."""
        connectors = ["searxng"]  # Always include general search

        # Intent-based suggestions
        if intent == QueryIntent.ACADEMIC:
            connectors.extend(["arxiv", "semantic_scholar"])
        elif intent == QueryIntent.CURRENT_EVENTS:
            connectors.extend(["news", "tavily"])
        elif intent == QueryIntent.TUTORIAL:
            connectors.extend(["stackoverflow", "github"])

        # Entity-based suggestions
        code_entities = {'python', 'javascript', 'typescript', 'rust', 'go'}
        if any(e.lower() in code_entities for e in entities):
            if "github" not in connectors:
                connectors.append("github")
            if "stackoverflow" not in connectors:
                connectors.append("stackoverflow")

        # Temporal freshness needs
        if temporal in (TemporalFocus.RECENT, TemporalFocus.CURRENT):
            if "tavily" not in connectors:
                connectors.append("tavily")

        return connectors

    def _compute_freshness_weight(
        self,
        temporal: TemporalFocus,
        intent: QueryIntent
    ) -> float:
        """Compute how much to weight freshness in ranking."""
        if temporal == TemporalFocus.CURRENT:
            return 0.4
        if temporal == TemporalFocus.RECENT:
            return 0.3
        if intent == QueryIntent.CURRENT_EVENTS:
            return 0.35
        if intent == QueryIntent.ACADEMIC:
            return 0.05  # Academic papers can be older
        return 0.1  # Default

    def _determine_depth(self, intent: QueryIntent, query: str) -> str:
        """Determine appropriate depth for synthesis."""
        if intent == QueryIntent.FACTUAL:
            return "brief"
        if intent == QueryIntent.COMPARATIVE:
            return "comprehensive"
        if intent == QueryIntent.ACADEMIC:
            return "comprehensive"
        if len(query.split()) > 15:  # Long queries often need detailed answers
            return "comprehensive"
        return "medium"
