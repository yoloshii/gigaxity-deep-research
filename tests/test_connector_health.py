"""Connector doctor — liveness probes, state trichotomy, isolation, no URL leaks."""

import asyncio
import time

import httpx
import pytest

from src.connectors import base as base_mod
from src.connectors.base import Connector, ConnectorHealth, SearchResult
from src.connectors.doctor import check_connectors
from src.connectors.searxng import SearXNGConnector
from src.connectors.tavily import TavilyConnector


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient: returns a response, raises, or sleeps."""

    raise_exc: Exception | None = None
    status_code: int = 200
    sleep_s: float = 0.0
    raise_for_url_substr: str | None = None
    last_init_kwargs: dict | None = None

    def __init__(self, *args, **kwargs):
        type(self).last_init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url):
        if type(self).sleep_s:
            await asyncio.sleep(type(self).sleep_s)
        substr = type(self).raise_for_url_substr
        if substr is not None and substr in str(url):
            raise httpx.ConnectError("down")
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return _FakeResponse(type(self).status_code)


@pytest.fixture
def fake_http(monkeypatch):
    """Patch httpx.AsyncClient inside base.py; yield the fake class."""
    _FakeAsyncClient.raise_exc = None
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.sleep_s = 0.0
    _FakeAsyncClient.raise_for_url_substr = None
    _FakeAsyncClient.last_init_kwargs = None
    monkeypatch.setattr(base_mod.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


class _StubConnector(Connector):
    """Minimal concrete connector for doctor tests."""

    def __init__(self, name, configured=True, probe_url="http://probe.test"):
        self.name = name
        self._configured = configured
        self._url = probe_url

    def is_configured(self):
        return self._configured

    def _probe_url(self):
        return self._url

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        return SearchResult(sources=[], query=query, connector_name=self.name)


class _ExplodingConnector(_StubConnector):
    async def health(self, timeout_s: float = 2.0) -> ConnectorHealth:
        raise RuntimeError("probe machinery itself broke")


class _HangingConnector(_StubConnector):
    """An override that ignores timeout_s entirely."""

    async def health(self, timeout_s: float = 2.0) -> ConnectorHealth:
        await asyncio.sleep(30)
        return ConnectorHealth(self.name, True, "ok")


@pytest.mark.unit
class TestHealthTrichotomy:
    def test_unconfigured_reports_unconfigured_without_network(self, monkeypatch):
        # If this ever touches the network the sabotage below explodes.
        def _boom(*a, **k):
            raise AssertionError("unconfigured probe must not construct a client")

        monkeypatch.setattr(base_mod.httpx, "AsyncClient", _boom)
        conn = _StubConnector("stub", configured=False)
        result = asyncio.run(conn.health())
        assert result.status == "unconfigured"
        assert result.configured is False

    def test_endpoint_answering_any_http_status_is_ok(self, fake_http):
        # API roots commonly 404/405 a bare GET — that still proves liveness.
        fake_http.status_code = 405
        result = asyncio.run(_StubConnector("stub").health())
        assert result.status == "ok"
        assert result.configured is True
        assert result.latency_ms is not None

    def test_connect_error_is_unreachable(self, fake_http):
        fake_http.raise_exc = httpx.ConnectError("boom")
        result = asyncio.run(_StubConnector("stub").health())
        assert result.status == "unreachable"

    def test_timeout_is_unreachable(self, fake_http):
        fake_http.raise_exc = httpx.ConnectTimeout("too slow")
        result = asyncio.run(_StubConnector("stub").health())
        assert result.status == "unreachable"

    def test_wall_clock_deadline_cuts_a_slow_drip(self, fake_http):
        """httpx timeouts are per-phase; the probe must bound TOTAL elapsed."""
        fake_http.sleep_s = 5.0
        start = time.perf_counter()
        result = asyncio.run(_StubConnector("stub").health(timeout_s=0.2))
        elapsed = time.perf_counter() - start
        assert result.status == "unreachable"
        assert result.detail in ("TimeoutError", "CancelledError")
        assert elapsed < 2.0

    def test_redirects_are_not_followed_and_a_3xx_is_ok(self, fake_http):
        fake_http.status_code = 301
        result = asyncio.run(_StubConnector("stub").health())
        assert result.status == "ok"
        assert fake_http.last_init_kwargs is not None
        assert fake_http.last_init_kwargs.get("follow_redirects") is False

    def test_failed_probes_still_report_latency(self, fake_http):
        fake_http.raise_exc = httpx.ConnectError("boom")
        result = asyncio.run(_StubConnector("stub").health())
        assert result.status == "unreachable"
        assert result.latency_ms is not None

    def test_no_probe_url_stays_ok_without_network(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("no-probe connector must not construct a client")

        monkeypatch.setattr(base_mod.httpx, "AsyncClient", _boom)
        conn = _StubConnector("stub", probe_url=None)
        result = asyncio.run(conn.health())
        assert result.status == "ok"


@pytest.mark.unit
class TestOutputBoundary:
    def test_detail_never_carries_the_probed_url(self, fake_http):
        """httpx error messages embed the URL; the detail must not."""
        secret_host = "http://internal-box.lan:8888"
        fake_http.raise_exc = httpx.ConnectError(
            f"All connection attempts failed for {secret_host}/"
        )
        result = asyncio.run(
            _StubConnector("stub", probe_url=secret_host).health()
        )
        assert secret_host not in result.detail
        assert "internal-box" not in result.detail
        assert result.detail == "ConnectError"

    def test_doctor_error_row_carries_exception_type_only(self):
        checks = asyncio.run(check_connectors([_ExplodingConnector("boomer")]))
        assert checks[0].status == "error"
        assert checks[0].detail == "RuntimeError"
        assert "machinery" not in checks[0].detail


@pytest.mark.unit
class TestDoctor:
    def test_hanging_health_override_cannot_stall_the_gather(self, fake_http):
        """The doctor enforces the deadline itself; a misbehaving override
        that ignores timeout_s becomes its own error row."""
        start = time.perf_counter()
        checks = asyncio.run(
            check_connectors(
                [_HangingConnector("hanger"), _StubConnector("good")],
                timeout_s=0.2,
            )
        )
        elapsed = time.perf_counter() - start
        by_name = {c.name: c for c in checks}
        assert by_name["hanger"].status == "error"
        assert by_name["hanger"].detail == "TimeoutError"
        assert by_name["hanger"].latency_ms is not None
        assert by_name["good"].status == "ok"
        assert elapsed < 5.0

    def test_one_exploding_connector_never_takes_down_the_report(self, fake_http):
        checks = asyncio.run(
            check_connectors(
                [
                    _StubConnector("good"),
                    _ExplodingConnector("boomer"),
                    _StubConnector("also-good"),
                ]
            )
        )
        by_name = {c.name: c for c in checks}
        assert by_name["good"].status == "ok"
        assert by_name["boomer"].status == "error"
        assert by_name["also-good"].status == "ok"

    def test_all_three_states_appear_side_by_side(self, fake_http):
        fake_http.raise_for_url_substr = "dead-host"
        ok = _StubConnector("up", probe_url="http://alive.test")
        down = _StubConnector("down", probe_url="http://dead-host.test")
        missing = _StubConnector("missing", configured=False)
        checks = asyncio.run(check_connectors([ok, down, missing]))
        statuses = {c.name: c.status for c in checks}
        assert statuses == {"up": "ok", "down": "unreachable", "missing": "unconfigured"}

    def test_probes_run_in_parallel_not_serially(self, fake_http):
        fake_http.sleep_s = 0.3
        conns = [_StubConnector(f"c{i}") for i in range(3)]
        start = time.perf_counter()
        checks = asyncio.run(check_connectors(conns))
        elapsed = time.perf_counter() - start
        assert all(c.status == "ok" for c in checks)
        # Serial would be >= 0.9s; parallel is ~0.3s. Generous margin for CI.
        assert elapsed < 0.75

    def test_default_report_covers_the_known_connector_set(self, monkeypatch, fake_http):
        # Unconfigured connectors are still reported — that's the point.
        from src.config import settings

        monkeypatch.setattr(settings, "searxng_host", "")
        monkeypatch.setattr(settings, "tavily_api_key", "")
        monkeypatch.setattr(settings, "linkup_api_key", "")
        checks = asyncio.run(check_connectors())
        assert {c.name for c in checks} == {"searxng", "tavily", "linkup"}
        assert all(c.status == "unconfigured" for c in checks)


@pytest.mark.unit
class TestRealConnectorProbes:
    def test_searxng_probes_its_documented_healthz(self):
        conn = SearXNGConnector(host="http://searx.test:8888/")
        assert conn._probe_url() == "http://searx.test:8888/healthz"

    def test_tavily_probe_is_the_public_api_root(self, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "tavily_api_key", "k")
        assert TavilyConnector()._probe_url() == "https://api.tavily.com"

    def test_linkup_probe_is_the_public_api_root(self, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "linkup_api_key", "k")
        from src.connectors.linkup import LinkUpConnector

        assert LinkUpConnector()._probe_url() == "https://api.linkup.so"


@pytest.mark.unit
class TestEndpoint:
    def test_health_connectors_endpoint_reports_probed_states(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.api import routes as routes_mod
        from src.main import app

        async def _fake_check(connectors=None, timeout_s=2.0):
            return [
                ConnectorHealth("searxng", True, "ok", "HTTP 200", 12),
                ConnectorHealth("tavily", True, "unreachable", "ConnectError"),
                ConnectorHealth("linkup", False, "unconfigured", "missing key/host"),
            ]

        monkeypatch.setattr(routes_mod, "check_connectors", _fake_check)
        client = TestClient(app)
        resp = client.get("/api/v1/health/connectors")
        assert resp.status_code == 200
        body = resp.json()
        by_name = {c["name"]: c for c in body["connectors"]}
        assert by_name["searxng"]["status"] == "ok"
        assert by_name["searxng"]["latency_ms"] == 12
        assert by_name["tavily"]["status"] == "unreachable"
        assert by_name["linkup"]["configured"] is False
        # No hybrid-local backend in this build: the report is connectors only.
        assert "local_llm" not in by_name

    def test_plain_health_still_makes_no_network_calls(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.main import app

        def _boom(*a, **k):
            raise AssertionError("/health must never construct an HTTP client")

        monkeypatch.setattr(base_mod.httpx, "AsyncClient", _boom)
        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        # Exact legacy shape — no additive fields on the fast endpoint.
        assert set(resp.json().keys()) == {"status", "connectors", "llm_configured"}
