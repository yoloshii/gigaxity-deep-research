"""Connector doctor — real liveness probes across the connector plane.

is_configured() answers capability (does a key/host exist); this module
answers liveness (does the endpoint actually respond right now). The two
are deliberately separate, and the aggregator's per-run selection (policy)
is a third question this module does not touch.

Unconfigured connectors ARE included in the report — surfacing all three
states side by side is the point.
"""

import asyncio
import logging
import time

from .base import Connector, ConnectorHealth
from . import SearXNGConnector, TavilyConnector, LinkUpConnector

logger = logging.getLogger(__name__)


def known_connectors() -> list[Connector]:
    """Every connector this build knows, configured or not."""
    return [SearXNGConnector(), TavilyConnector(), LinkUpConnector()]


async def check_connectors(
    connectors: list[Connector] | None = None,
    timeout_s: float = 2.0,
) -> list[ConnectorHealth]:
    """Probe every connector in parallel with per-connector isolation.

    One misbehaving connector must never take the whole report down: an
    unexpected exception from a probe becomes that connector's own
    "error" row (exception TYPE only — messages can embed endpoint URLs).
    The deadline is enforced HERE too, not only inside the base
    Connector.health(): an override that ignores timeout_s must not stall
    the gather. The outer deadline carries a 1s grace so the base
    implementation's own deadline fires first and classifies a slow
    endpoint as "unreachable"; the outer one only catches a misbehaving
    override, as that connector's "error" row.
    """
    conns = known_connectors() if connectors is None else connectors

    async def _safe(conn: Connector) -> ConnectorHealth:
        start = time.perf_counter()
        try:
            async with asyncio.timeout(timeout_s + 1.0):
                return await conn.health(timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 — isolation is the contract
            logger.warning("connector health probe failed: %s", type(exc).__name__)
            latency_ms = int((time.perf_counter() - start) * 1000)
            try:
                configured = conn.is_configured()
            except Exception:  # noqa: BLE001
                configured = False
            return ConnectorHealth(
                conn.name, configured, "error", type(exc).__name__, latency_ms
            )

    return list(await asyncio.gather(*(_safe(c) for c in conns)))
