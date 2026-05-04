"""Base connector types and protocol."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Source:
    """Represents a source document with metadata."""

    id: str
    title: str
    url: str
    content: str
    score: float = 0.0
    connector: str = ""
    metadata: dict = field(default_factory=dict)

    def to_citation(self) -> str:
        """Format as citation reference."""
        return f"[{self.id}] {self.title} - {self.url}"


@dataclass
class SearchResult:
    """Result from a connector search."""

    sources: list[Source]
    query: str
    connector_name: str
    total_results: int = 0


class Connector(ABC):
    """Base connector protocol for search sources."""

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute search and return results."""
        ...

    def is_configured(self) -> bool:
        """Check if connector is properly configured."""
        return True
