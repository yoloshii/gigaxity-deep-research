"""Base connector types and protocol."""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx


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


HealthStatus = Literal["ok", "unreachable", "unconfigured", "error"]


@dataclass
class ConnectorHealth:
    """Result of one connector liveness probe.

    Three states are deliberately distinct — config-present, scheduled, and
    endpoint-alive are different questions:
      - "unconfigured": is_configured() is False (missing key/host)
      - "unreachable": configured, but the endpoint did not answer HTTP
      - "ok": configured and the endpoint answered ANY HTTP status
      - "error": the probe itself failed unexpectedly

    "ok" proves reachability only — it does NOT validate credentials for
    paid APIs (that would spend a billed call).
    """

    name: str
    configured: bool
    status: HealthStatus
    detail: str = ""
    latency_ms: int | None = None


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

    def _probe_url(self) -> str | None:
        """Endpoint probed by health(); None = no liveness probe defined."""
        return None

    async def health(self, timeout_s: float = 2.0) -> ConnectorHealth:
        """Classify this connector as unconfigured / unreachable / ok.

        Really contacts the endpoint (unlike is_configured, which only
        checks config presence). Any HTTP response counts as reachable —
        API roots commonly answer 404/405 to a bare GET, and a redirect
        already proves liveness, so redirects are not followed. The whole
        probe runs under a wall-clock deadline of timeout_s (httpx's own
        timeout is per-phase inactivity, not total elapsed). Details carry
        the exception TYPE only, never the message: httpx error messages
        embed the probed URL, and the host may be internal.
        """
        if not self.is_configured():
            return ConnectorHealth(self.name, False, "unconfigured", "missing key/host")
        url = self._probe_url()
        if not url:
            return ConnectorHealth(self.name, True, "ok", "configured (no probe defined)")
        start = time.perf_counter()
        try:
            async with asyncio.timeout(timeout_s):
                async with httpx.AsyncClient(
                    timeout=timeout_s, follow_redirects=False
                ) as client:
                    resp = await client.get(url)
        except (httpx.HTTPError, TimeoutError) as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return ConnectorHealth(
                self.name, True, "unreachable", type(exc).__name__, latency_ms
            )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ConnectorHealth(
            self.name, True, "ok", f"HTTP {resp.status_code}", latency_ms
        )
