"""Regression tests for the jina-mcp companion's 422/42206 zero-results handling.

s.jina.ai encodes an empty SERP as HTTP 422 AssertionFailureError / status 42206
(observed 2026-08-04) instead of an empty data array. The companion classifies
that exact signature as a benign no-results outcome; everything else must keep
today's diagnosed `Search failed` path. Design codex-cleared in session
019fc995 turns 8-10 ("Zero remaining findings — ship as is.").
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "companions" / "jina-mcp" / "mcp_server.py"
)

ZERO_BODY = json.dumps(
    {
        "data": None,
        "code": 422,
        "name": "AssertionFailureError",
        "status": 42206,
        "message": 'No search results available for query "sqlite wal checkpoint"',
    }
)


@pytest.fixture(scope="module")
def jina():
    # Module import hard-exits without a key (mcp_server.py:71).
    os.environ.setdefault("JINA_API_KEY", "test-key-not-real")
    spec = importlib.util.spec_from_file_location("jina_companion_mcp", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["jina_companion_mcp"] = module
    spec.loader.exec_module(module)
    return module


def _failure(jina, status: int, body: str):
    return jina._Failure(jina._diagnose(status, body), status=status, body=body)


class TestFailureCarrier:
    def test_failure_preserves_str_behavior(self, jina):
        f = jina._Failure("diagnosed text", status=500, body="raw body")
        assert isinstance(f, str)
        assert f == "diagnosed text"
        assert f"{f}" == "diagnosed text"
        assert f.status == 500
        assert f.body == "raw body"

    def test_request_failure_carries_status_and_body(self, jina, monkeypatch):
        import urllib.error
        import io

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 422, "Unprocessable", {}, io.BytesIO(ZERO_BODY.encode())
            )

        monkeypatch.setattr(jina.urllib.request, "urlopen", boom)
        ok, payload = jina._request("https://s.jina.ai/?q=x")
        assert not ok
        assert isinstance(payload, jina._Failure)
        assert payload.status == 422
        assert payload.body == ZERO_BODY


class TestIsZeroResults:
    def test_genuine_42206_signature(self, jina):
        assert jina._is_zero_results(_failure(jina, 422, ZERO_BODY)) is True

    def test_ordinary_422_object(self, jina):
        body = json.dumps({"code": 422, "status": 42201, "message": "bad input"})
        assert jina._is_zero_results(_failure(jina, 422, body)) is False

    def test_status_mismatch(self, jina):
        body = json.dumps(
            {"status": 42207, "name": "AssertionFailureError",
             "message": "No search results available for query x"}
        )
        assert jina._is_zero_results(_failure(jina, 422, body)) is False

    def test_name_mismatch(self, jina):
        body = json.dumps(
            {"status": 42206, "name": "SomethingElse",
             "message": "No search results available for query x"}
        )
        assert jina._is_zero_results(_failure(jina, 422, body)) is False

    def test_message_missing_phrase(self, jina):
        body = json.dumps(
            {"status": 42206, "name": "AssertionFailureError", "message": "nope"}
        )
        assert jina._is_zero_results(_failure(jina, 422, body)) is False

    def test_valid_json_null_body(self, jina):
        assert jina._is_zero_results(_failure(jina, 422, "null")) is False

    def test_valid_json_array_body(self, jina):
        assert jina._is_zero_results(_failure(jina, 422, "[]")) is False

    def test_valid_json_scalar_bodies(self, jina):
        assert jina._is_zero_results(_failure(jina, 422, '"text"')) is False
        assert jina._is_zero_results(_failure(jina, 422, "5")) is False

    def test_truncated_body(self, jina):
        assert jina._is_zero_results(_failure(jina, 422, ZERO_BODY[:40])) is False

    def test_wrong_http_status_with_phrase(self, jina):
        for status in (401, 402):
            body = json.dumps(
                {"status": 42206, "name": "AssertionFailureError",
                 "message": "No search results available for query x"}
            )
            assert jina._is_zero_results(_failure(jina, status, body)) is False

    def test_plain_string_payload(self, jina):
        assert jina._is_zero_results("Network error contacting s.jina.ai: x") is False


class TestSearchWebClassification:
    def _patch(self, monkeypatch, jina, payload):
        monkeypatch.setattr(jina, "_json_request", lambda url, **kw: (False, payload))

    def test_zero_results_renders_benign(self, jina, monkeypatch):
        self._patch(monkeypatch, jina, _failure(jina, 422, ZERO_BODY))
        out = jina._search_web("q", 10, None, None, None, None)
        assert out.startswith("No results for 'q'")
        assert "Search failed" not in out
        assert "Broaden the query or loosen quotes/filters" in out

    def test_ordinary_422_renders_failure(self, jina, monkeypatch):
        body = json.dumps({"code": 422, "status": 42201, "message": "bad input"})
        self._patch(monkeypatch, jina, _failure(jina, 422, body))
        out = jina._search_web("q", 10, None, None, None, None)
        assert out.startswith("Search failed for 'q'")

    def test_422_null_and_array_render_failure_without_exception(self, jina, monkeypatch):
        for raw in ("null", "[]"):
            self._patch(monkeypatch, jina, _failure(jina, 422, raw))
            out = jina._search_web("q", 10, None, None, None, None)
            assert out.startswith("Search failed for 'q'")

    def test_auth_and_quota_with_embedded_phrase_render_failure(self, jina, monkeypatch):
        for status in (401, 402):
            body = json.dumps(
                {"message": "no search results available (auth wall)"}
            )
            self._patch(monkeypatch, jina, _failure(jina, status, body))
            out = jina._search_web("q", 10, None, None, None, None)
            assert out.startswith("Search failed for 'q'")

    def test_plain_string_failure_unchanged(self, jina, monkeypatch):
        self._patch(monkeypatch, jina, "Network error contacting s.jina.ai: refused")
        out = jina._search_web("q", 10, None, None, None, None)
        assert out.startswith("Search failed for 'q'")


class TestParallelMixedBatch:
    def test_mixed_batch_preserves_order_and_shapes(self, jina, monkeypatch):
        def fake(url, **kw):
            if "zero" in url:
                return False, _failure(jina, 422, ZERO_BODY)
            return True, {
                "data": [{"title": "T", "url": "https://x.example", "description": "d"}]
            }

        monkeypatch.setattr(jina, "_json_request", fake)
        fn = jina.parallel_search_web
        fn = getattr(fn, "fn", fn)
        out = fn(["zero phrase", "normal query"], num=3)
        zero_idx = out.index("No results for 'zero phrase'")
        normal_idx = out.index("Results for 'normal query'")
        assert zero_idx < normal_idx
        assert "Search failed" not in out
