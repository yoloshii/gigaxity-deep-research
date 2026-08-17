"""Transport-agnostic progress reporting for long-running tool calls.

Why this exists
---------------
An MCP client typically enforces two independent deadlines on a tool call:

* a **hard** wall-clock cap that progress notifications do NOT extend, and
* an **idle** cap that IS reset every time a progress notification arrives.

A pipeline that emits nothing runs the idle timer uninterrupted for its whole
duration, so a long synthesis is aborted client-side mid-flight — while the
server is still working, and without ever getting to report a failure. This
module is the emitting half of the fix; `llm_client.chat_completion` is where it
is wired, because every LLM call in this server funnels through that one method.

Design constraints
------------------
1. **No MCP coupling below this module.** The synthesis and discovery packages
   call `tick()` and never touch a FastMCP ``Context``. The server owns the only
   adapter, so the pipeline stays unit-testable and usable as a library.
2. **ContextVar, not parameter threading.** The var is set once around the whole
   request and is inherited by child tasks (``asyncio.gather`` copies the
   context), so concurrent per-source work reports without any signature change.
   It is installed with a token and reset in a ``finally`` so a sequential
   in-process caller cannot inherit a previous request's reporter.
3. **Monotonic under concurrency.** MCP requires the progress value to increase.
   Concurrent callers would interleave a naive counter, so increment-and-send
   happens under one lock owned by a single request-scoped reporter.
4. **A dead transport must not be swallowed.** Over stdio, notifications and the
   final response share one write stream: if a send fails with a terminal
   closure the result can never be delivered, so continuing is wasted spend. The
   owning task is cancelled instead. ``CancelledError`` derives from
   ``BaseException`` and so passes through the pipeline's broad
   ``except Exception`` handlers rather than being mislabelled an LLM failure.
   Any other reporting error is logged once and disables reporting for the
   request — reporting is best-effort, the synthesis is not.
5. **Reporting must not become the stall it prevents.** The send is bounded by
   its own timeout, because the lock is held across it: a sink that *hangs*
   rather than raises would otherwise block every concurrent call forever.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar, Token
from typing import Awaitable, Callable, Optional

from .config import settings

logger = logging.getLogger(__name__)

# Raised by anyio when the peer is gone. Imported defensively: anyio arrives via
# the MCP SDK, and this module must stay importable in a bare library install.
try:  # pragma: no cover - trivial import guard
    from anyio import BrokenResourceError, ClosedResourceError

    _TERMINAL_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
        BrokenResourceError,
        ClosedResourceError,
    )
except ImportError:  # pragma: no cover
    _TERMINAL_TRANSPORT_ERRORS = ()


SendFn = Callable[[float, str], Awaitable[None]]

HEARTBEAT_MESSAGE = "model call still running"


class ProgressReporter:
    """Request-scoped, serialized progress counter.

    `send` is the transport adapter — `(progress, message) -> awaitable`. Keeping
    it a plain callable is what keeps this module free of any MCP import.
    `tool_name` prefixes every message, so one shared instrumentation point can
    serve tools with different names without threading a label through the
    pipeline's call signatures.
    """

    def __init__(self, send: SendFn, tool_name: str = "") -> None:
        self._send = send
        self._tool = tool_name
        self._lock = asyncio.Lock()
        self._n = 0.0
        self._disabled = False
        # Captured HERE, not at send time. The terminal-closure branch below must
        # cancel the REQUEST, and `asyncio.current_task()` is only the request
        # task when tick() runs on it. A heartbeat tick runs on the heartbeat
        # task, so reading it there would cancel the heartbeat and leave the
        # request running against a dead transport.
        try:
            self._owner: Optional[asyncio.Task] = asyncio.current_task()
        except RuntimeError:  # pragma: no cover - constructed outside a loop
            self._owner = None

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def count(self) -> float:
        """Notifications successfully sent. Test seam: a call site that forgot
        to `await tick()` leaves this at 0 while still looking correct."""
        return self._n

    async def tick(self, message: str) -> None:
        if self._disabled:
            return
        text = f"{self._tool}: {message}" if self._tool else message
        async with self._lock:
            if self._disabled:
                return
            self._n += 1
            n = self._n
            try:
                # Bounded: the lock is held across this await, so a sink that
                # hangs rather than raises would stall every concurrent call.
                async with asyncio.timeout(settings.progress_send_timeout):
                    await self._send(n, text)
            except _TERMINAL_TRANSPORT_ERRORS as exc:
                # The write stream is gone, so the result cannot be delivered
                # either. Cancel rather than raise: CancelledError is a
                # BaseException and survives the broad `except Exception`
                # handlers downstream, which would otherwise record this as an
                # LLM failure.
                self._disabled = True
                logger.warning(
                    "progress transport closed (%s); cancelling the request",
                    type(exc).__name__,
                )
                task = self._owner or asyncio.current_task()
                if task is not None:
                    task.cancel()
                # Deliberately NOT re-raised: anyio's closure errors subclass
                # Exception, so propagating one here would be caught by those
                # same broad handlers and mislabelled. Returning lets the
                # requested cancellation surface as CancelledError at the next
                # await instead, which passes through them untouched.
                return
            except Exception:
                # Best-effort: never let reporting fail a synthesis. Disable so a
                # persistently broken sink cannot re-log on every subsequent call.
                self._disabled = True
                logger.warning(
                    "progress reporting disabled after a send failure", exc_info=True
                )


_reporter: ContextVar[Optional[ProgressReporter]] = ContextVar(
    "gigaxity_progress_reporter", default=None
)


def install(reporter: Optional[ProgressReporter]) -> Token:
    """Install the request's reporter. ALWAYS pair with `reset(token)` in a
    `finally` — an un-reset var leaks into the next sequential call in the same
    task."""
    return _reporter.set(reporter)


def reset(token: Token) -> None:
    _reporter.reset(token)


def current() -> Optional[ProgressReporter]:
    return _reporter.get()


async def tick(message: str) -> None:
    """Report one unit of progress. No-op when nothing is installed, which is the
    normal case for direct library callers, REST requests and unit tests."""
    reporter = _reporter.get()
    if reporter is None:
        return
    await reporter.tick(message)


def start_heartbeat() -> Optional[asyncio.Task]:
    """Tick periodically until cancelled, so one long call is not one long silence.

    Entry and settlement ticks alone cannot subdivide a single model call, and a
    non-streaming completion is silent for its entire duration — which is the
    whole exposure on a slow endpoint. This closes that.

    Contract, fixed so no caller has to decide any of it:

    * returns ``None`` when no reporter is installed, so the caller's
      ``if hb is not None`` is the only guard needed;
    * captures the reporter object **once** rather than reading the ContextVar
      each iteration, so a reset mid-call cannot retarget the task;
    * waits a **full interval before the first tick**, so a fast call emits none;
    * reads the interval once, at creation;
    * exits when cancelled or when the reporter disables itself;
    * never raises into the caller — ``tick`` already swallows send failures.
    """
    reporter = _reporter.get()
    if reporter is None:
        return None

    interval = settings.progress_heartbeat_interval

    async def _beat() -> None:
        while not reporter.disabled:
            await asyncio.sleep(interval)
            if reporter.disabled:
                return
            await reporter.tick(HEARTBEAT_MESSAGE)

    return asyncio.create_task(_beat())
