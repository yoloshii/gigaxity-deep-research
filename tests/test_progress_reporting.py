"""Progress notifications for long-running tool calls.

An MCP client runs two deadlines: a hard wall-clock cap that progress does NOT
extend, and an idle cap that a progress notification DOES reset. This server
emitted nothing, so the idle timer ran uninterrupted for a whole synthesis.

Instrumentation lives in `llm_client.chat_completion` because every server-owned
LLM call reaches the SDK through it — MCP tools, REST routes and the synthesis
engine alike. A client injected into `SynthesisEngine` bypasses the wrapper and
is therefore out of coverage by construction. These tests cover the emission
machinery, the production wiring, and the failure modes that would make either
vacuous.
"""

import asyncio
import types

import pytest

from src import progress
from src.config import settings
from src.llm_client import LLMClient


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class FakeReporter(progress.ProgressReporter):
    """Records deliveries. Subclasses the real reporter so the lock, counter,
    label prefixing and error handling under test are the production ones."""

    def __init__(self, fail_with: BaseException | None = None, hang: bool = False,
                 tool_name: str = ""):
        self.messages: list[str] = []
        self.values: list[float] = []
        self._fail_with = fail_with
        self._hang = hang

        async def _send(value: float, message: str) -> None:
            if self._hang:
                await asyncio.sleep(3600)
            if self._fail_with is not None:
                raise self._fail_with
            self.values.append(value)
            self.messages.append(message)

        super().__init__(_send, tool_name)


class _FakeSDK:
    """Stands in for `AsyncOpenAI`, capturing constructor kwargs and requests."""

    def __init__(self, behaviour=None, delay: float = 0.0):
        self.requests: list[dict] = []
        outer = self

        class _Completions:
            async def create(self, **kw):
                outer.requests.append(kw)
                if delay:
                    await asyncio.sleep(delay)
                if isinstance(behaviour, BaseException):
                    raise behaviour
                # A realistic shape: callers read `choices[0].message`, so an
                # empty list would fail them for a reason unrelated to progress.
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content="answer", reasoning_content=None, reasoning=None),
                        finish_reason="stop",
                    )]
                )

        self.chat = types.SimpleNamespace(completions=_Completions())


def _client(monkeypatch, behaviour=None, delay: float = 0.0) -> LLMClient:
    c = LLMClient(api_key="k")
    monkeypatch.setattr(c, "_client", _FakeSDK(behaviour, delay))
    return c


@pytest.fixture
def reporter():
    r = FakeReporter(tool_name="synthesize")
    token = progress.install(r)
    yield r
    progress.reset(token)


@pytest.fixture(autouse=True)
def _fast_heartbeat(monkeypatch):
    """Keep the suite quick without changing the code path under test."""
    monkeypatch.setattr(settings, "progress_heartbeat_interval", 1)


# --------------------------------------------------------------------------
# the emission contract
# --------------------------------------------------------------------------


async def test_success_emits_entry_and_settlement(reporter, monkeypatch):
    await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])
    assert len(reporter.messages) == 2, reporter.messages
    assert "started" in reporter.messages[0]
    assert "returned" in reporter.messages[1]


async def test_failure_still_emits_settlement(reporter, monkeypatch):
    """The hole a success-only scheme leaves: a failed chain gets swallowed by a
    broad `except Exception` upstream and is followed by ANOTHER silent call, so
    the whole sequence blows the idle window."""
    with pytest.raises(ValueError):
        await _client(monkeypatch, ValueError("upstream exploded")).chat_completion(
            messages=[{"role": "user", "content": "x"}]
        )
    assert len(reporter.messages) == 2
    assert "failed" in reporter.messages[1]


async def test_messages_carry_the_tool_name(reporter, monkeypatch):
    """One shared instrumentation point serves tools with different names, so the
    label must come from the reporter — a hardcoded one would mislabel every
    non-synthesis caller of the same choke point."""
    await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])
    assert all(m.startswith("synthesize: ") for m in reporter.messages), reporter.messages


async def test_external_cancellation_performs_no_settlement_io(reporter, monkeypatch):
    """`CancelledError` is a BaseException, so it matches neither `except
    Exception` nor `else`. It must propagate without awaiting transport I/O — an
    unconditional `finally` here would delay or mask the cancellation."""
    with pytest.raises(asyncio.CancelledError):
        await _client(monkeypatch, asyncio.CancelledError()).chat_completion(
            messages=[{"role": "user", "content": "x"}]
        )
    assert len(reporter.messages) == 1, "settlement tick ran during cancellation"


async def test_ticks_are_delivered_not_merely_called(reporter, monkeypatch):
    """Counts DELIVERIES, not call sites. A call site that built the coroutine
    without awaiting it would leave this at zero while still looking correct —
    the exact silent no-op this whole fix exists to remove."""
    await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])
    assert reporter.count == 2
    assert reporter.values == [1.0, 2.0]


async def test_values_stay_monotonic_under_concurrency(reporter, monkeypatch):
    """RCS runs per-source calls concurrently; MCP requires progress to increase."""
    clients = [_client(monkeypatch, delay=0.01) for _ in range(12)]
    await asyncio.gather(*(c.chat_completion(messages=[{"role": "user", "content": "x"}])
                           for c in clients))
    assert reporter.values == sorted(reporter.values)
    assert len(set(reporter.values)) == len(reporter.values), "duplicate progress values"


async def test_no_reporter_installed_is_a_silent_noop(monkeypatch):
    """REST requests, library callers and most of the suite run with no context."""
    assert progress.current() is None
    await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])


async def test_reporter_does_not_leak_between_sequential_calls():
    token = progress.install(FakeReporter())
    progress.reset(token)
    assert progress.current() is None, "reporter leaked past its reset"


# --------------------------------------------------------------------------
# the heartbeat
# --------------------------------------------------------------------------


async def test_heartbeat_fires_during_a_long_call(reporter, monkeypatch):
    """Entry and settlement cannot subdivide one completion, and a non-streaming
    call is silent for its whole duration. This is what covers that."""
    await _client(monkeypatch, delay=2.5).chat_completion(
        messages=[{"role": "user", "content": "x"}]
    )
    beats = [m for m in reporter.messages if progress.HEARTBEAT_MESSAGE in m]
    assert beats, reporter.messages
    assert reporter.messages[-1].endswith("model call returned"), (
        "a heartbeat landed after settlement — ordering is broken"
    )


async def test_fast_call_emits_no_heartbeat(reporter, monkeypatch):
    """One full interval must elapse first, so ordinary calls stay quiet."""
    await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])
    assert not [m for m in reporter.messages if progress.HEARTBEAT_MESSAGE in m]


async def test_heartbeat_is_cancelled_before_settlement(reporter, monkeypatch):
    """No beat may land after `returned`/`failed`: progress must not regress and
    a stray beat after settlement is a leaked task."""
    await _client(monkeypatch, delay=1.5).chat_completion(
        messages=[{"role": "user", "content": "x"}]
    )
    before = len(reporter.messages)
    await asyncio.sleep(1.5)
    assert len(reporter.messages) == before, "heartbeat survived the call"


async def test_no_heartbeat_task_without_a_reporter():
    assert progress.current() is None
    assert progress.start_heartbeat() is None


async def test_cancellation_during_heartbeat_cleanup_propagates(reporter, monkeypatch):
    """The subtle one, and it has to be aimed precisely.

    `heartbeat.cancel(); try: await heartbeat; except CancelledError: pass` ALSO
    swallows a cancellation delivered to the REQUEST task while it waits on that
    cleanup — the request then sails on to settlement as though nothing happened.
    `asyncio.gather(..., return_exceptions=True)` returns the child's
    cancellation as a value while leaving an outer cancellation intact.

    Cancelling mid-model-call does NOT test this: that cancellation lands before
    cleanup begins and the rejected implementation passes it too. So the fake
    heartbeat resists teardown, signals once the request is parked inside
    cleanup, and only then is the request cancelled — the one window where the
    two implementations differ.
    """
    in_cleanup = asyncio.Event()

    async def _stubborn():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            in_cleanup.set()        # the request is now awaiting our teardown
            await asyncio.sleep(0.3)  # hold it there
            raise

    monkeypatch.setattr(progress, "start_heartbeat",
                        lambda: asyncio.create_task(_stubborn()))

    c = _client(monkeypatch, delay=0.05)
    task = asyncio.create_task(c.chat_completion(messages=[{"role": "user", "content": "x"}]))
    await asyncio.wait_for(in_cleanup.wait(), timeout=5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "cancellation was swallowed by heartbeat cleanup"
    assert not any("returned" in m for m in reporter.messages), (
        "the request settled after being cancelled during cleanup"
    )


# --------------------------------------------------------------------------
# the wall-clock cap
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad",
    [
        ("llm_max_retries", -1),
        ("progress_send_timeout", 0),
        ("progress_heartbeat_interval", 0),
    ],
)
def test_invalid_values_are_rejected_at_construction(field, bad):
    """A packaged user never runs pytest, so the bounds have to hold at startup.

    Without this, deleting `ge=0` / `gt=0` leaves the suite green while a
    deployment silently accepts `RESEARCH_LLM_MAX_RETRIES=-1` — which the SDK
    turns into zero attempts and then a "should never happen" assertion.
    """
    from pydantic import ValidationError
    from src.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: bad})


async def test_cap_is_disabled_by_default():
    """A ceiling that kills healthy generation on a slow endpoint is a worse
    failure than having none, so it is opt-in."""
    assert settings.llm_wall_clock_cap == 0


async def test_disabled_cap_does_not_bound_the_call(reporter, monkeypatch):
    monkeypatch.setattr(settings, "llm_wall_clock_cap", 0)
    await _client(monkeypatch, delay=1.2).chat_completion(
        messages=[{"role": "user", "content": "x"}]
    )
    assert "returned" in reporter.messages[-1]


async def test_positive_cap_bounds_the_call_and_still_settles(reporter, monkeypatch):
    monkeypatch.setattr(settings, "llm_wall_clock_cap", 1)
    with pytest.raises(asyncio.TimeoutError):
        await _client(monkeypatch, delay=30).chat_completion(
            messages=[{"role": "user", "content": "x"}]
        )
    # The settlement tick must survive the expired timeout scope it reports on.
    assert "failed" in reporter.messages[-1]


async def test_cap_applies_without_a_reporter(monkeypatch):
    """It is a server-wide RESOURCE policy, not an MCP-derived one: REST and
    library callers get the same ceiling. Making it caller-dependent would mean
    identical work timing out on one surface and not the other."""
    assert progress.current() is None
    monkeypatch.setattr(settings, "llm_wall_clock_cap", 1)
    with pytest.raises(asyncio.TimeoutError):
        await _client(monkeypatch, delay=30).chat_completion(
            messages=[{"role": "user", "content": "x"}]
        )


# --------------------------------------------------------------------------
# reporting failures must never fail the work
# --------------------------------------------------------------------------


async def test_send_failure_disables_reporting_without_failing_the_call(monkeypatch):
    r = FakeReporter(fail_with=RuntimeError("sink is broken"))
    token = progress.install(r)
    try:
        await _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}])
        assert r.disabled, "a broken sink must be disabled, not retried every tick"
    finally:
        progress.reset(token)


async def test_a_hanging_sink_is_bounded(monkeypatch):
    """The lock is held across the send, so an unbounded hang would block every
    concurrent call — turning the reporting machinery into the stall it exists to
    prevent. A raising sink is not enough to cover this."""
    monkeypatch.setattr(settings, "progress_send_timeout", 1)
    r = FakeReporter(hang=True)
    token = progress.install(r)
    try:
        await asyncio.wait_for(
            _client(monkeypatch).chat_completion(messages=[{"role": "user", "content": "x"}]),
            timeout=10,
        )
        assert r.disabled
    finally:
        progress.reset(token)


async def test_terminal_transport_closure_cancels_the_request(monkeypatch):
    """Notifications and the final response share one stdio write stream, so a
    terminal closure means the result is undeliverable and continuing is wasted
    spend. Cancelling rather than raising is deliberate: `CancelledError` is a
    BaseException and passes through the pipeline's broad `except Exception`
    handlers instead of being mislabelled an LLM failure."""
    anyio = pytest.importorskip("anyio")

    async def _run():
        r = FakeReporter(fail_with=anyio.ClosedResourceError())
        token = progress.install(r)
        try:
            await _client(monkeypatch).chat_completion(
                messages=[{"role": "user", "content": "x"}]
            )
        finally:
            progress.reset(token)

    # A CHILD task: the reporter cancels whichever task owned it, which in
    # production is the tool handler but here would be the test itself.
    task = asyncio.create_task(_run())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_owner_task_is_captured_at_construction(monkeypatch):
    """A heartbeat tick runs on the HEARTBEAT task. Reading
    `asyncio.current_task()` at send time would cancel that task instead of the
    request, so a dead transport would silently leave the request running."""
    anyio = pytest.importorskip("anyio")

    async def _run():
        r = FakeReporter(fail_with=anyio.ClosedResourceError())
        token = progress.install(r)
        try:
            # Tick from a child task; the OWNER (this one) must be cancelled.
            await asyncio.create_task(r.tick("from a child"))
            await asyncio.sleep(1)
        finally:
            progress.reset(token)

    task = asyncio.create_task(_run())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "the owning request task should have been cancelled"


# --------------------------------------------------------------------------
# the production wiring
# --------------------------------------------------------------------------
#
# Everything above installs a reporter by hand. That leaves the real adapter
# untested: deleting `@_reports_progress`, or making `_make_request_reporter()`
# always return None, would keep the suite green while production emitted
# nothing at all.


class _FakeCtx:
    """Stands in for a FastMCP Context with a live progressToken."""

    def __init__(self):
        self.sent: list[tuple[float, str]] = []

    async def report_progress(self, progress: float, total=None, message: str = ""):
        self.sent.append((progress, message))


@pytest.fixture
def fake_ctx(monkeypatch):
    from src import mcp_server

    ctx = _FakeCtx()
    monkeypatch.setattr(mcp_server, "get_context", lambda: ctx)
    return ctx


# Every tool that reaches the model. `search` is excluded on purpose: it makes no
# LLM call, so decorating it would install a reporter that never ticks.
INSTRUMENTED_TOOLS = ("research", "ask", "discover", "synthesize", "reason")


async def test_make_request_reporter_is_none_without_an_mcp_request():
    """`get_context()` raises outside a request; direct library and test callers
    hit this path constantly, so it must be an ordinary None, not an error."""
    from src import mcp_server

    assert mcp_server._make_request_reporter("synthesize") is None


async def test_make_request_reporter_binds_to_the_fastmcp_context(fake_ctx):
    from src import mcp_server

    r = mcp_server._make_request_reporter("discover")
    assert r is not None
    await r.tick("hello")
    assert fake_ctx.sent == [(1.0, "discover: hello")]


@pytest.mark.parametrize("tool_name", INSTRUMENTED_TOOLS)
async def test_registered_tool_is_wired_for_progress(tool_name, fake_ctx):
    """Exercises the callable FASTMCP ACTUALLY REGISTERED, not the module export.

    With the decorators in the other order:

        @_reports_progress
        @mcp.tool()
        async def synthesize(...)

    the module export would be wrapped while FastMCP registered the UNWRAPPED
    function — a green suite and zero progress on every real MCP call. The
    identity assertion pins the two together so they cannot silently diverge.
    """
    from src import mcp_server

    tool = await mcp_server.mcp.get_tool(tool_name)
    registered = tool.fn
    assert registered is getattr(mcp_server, tool_name), (
        f"the registered callable and the module export for {tool_name} have "
        "diverged — check decorator order; @mcp.tool() must stay outermost"
    )
    assert hasattr(registered, "__wrapped__"), f"{tool_name} is not progress-wrapped"


async def test_search_is_deliberately_not_instrumented():
    """It makes no LLM call. A reporter here would emit an opening tick and then
    nothing — the misleading shape this coverage decision exists to avoid."""
    from src import mcp_server

    tool = await mcp_server.mcp.get_tool("search")
    assert not hasattr(tool.fn, "__wrapped__"), (
        "search was decorated; it makes no LLM call and would emit only an "
        "opening tick"
    )


async def test_decorator_installs_and_resets_around_the_call(fake_ctx):
    from src import mcp_server

    seen = {}

    @mcp_server._reports_progress
    async def probe():
        seen["during"] = progress.current()
        return "done"

    assert await probe() == "done"
    assert seen["during"] is not None, "no reporter installed for the call"
    assert progress.current() is None, "reporter not reset after the call"
    assert fake_ctx.sent and fake_ctx.sent[0][1] == "probe: starting"


@pytest.mark.parametrize("boom", [ValueError("x"), asyncio.CancelledError()])
async def test_decorator_resets_after_a_failure(fake_ctx, boom):
    from src import mcp_server

    @mcp_server._reports_progress
    async def probe():
        raise boom

    with pytest.raises(type(boom)):
        await probe()
    assert progress.current() is None, "reporter leaked after a failure"


async def test_wiring_reaches_the_model_boundary(fake_ctx, monkeypatch):
    """The end-to-end one: a decorated tool's LLM call must emit at the REAL
    model boundary, not merely install a reporter. Everything else in this
    section would still pass if `chat_completion` stopped ticking."""
    from src import mcp_server

    tool = await mcp_server.mcp.get_tool("ask")
    c = _client(monkeypatch)
    monkeypatch.setattr(mcp_server, "_get_llm_client", lambda *a, **k: c)
    monkeypatch.setattr(mcp_server, "get_llm_content", lambda *a, **k: "answer")

    await tool.fn(query="q")

    messages = [m for _, m in fake_ctx.sent]
    assert messages[0] == "ask: starting"
    assert "ask: model call started" in messages
    assert "ask: model call returned" in messages
