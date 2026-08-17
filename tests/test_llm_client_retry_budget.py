"""Retry-budget and endpoint-plumbing invariants for the LLM client.

The OpenAI SDK retries read-timeouts exactly as it retries 429s, so the retry
count multiplies `llm_timeout`: one stalled upstream connection costs roughly
`(retries + 1) * llm_timeout` of silent retrying. That product is how a request
outlives an MCP client's abort deadline while the server, still mid-chain, never
gets to report a failure.

What this module asserts is that the knob EXISTS and is wired, not that it holds
a particular value:

* the retry count is configurable and reaches the SDK, so a deployment can
  shorten a stalled chain deliberately;
* the shipped default matches the SDK's own, so installing this release changes
  no behaviour on its own;
* the endpoint, key and model reach the client from settings rather than from a
  literal.

Deliberately ABSENT: any assertion of the form
`(max_retries + 1) * timeout.read < SOME_CEILING`. That is not a wall-clock
bound — `httpx.Timeout` sets per-OPERATION timeouts and HTTP/1 reapplies the read
timeout to every individual network read, so a trickling response outlives the
product, and the SDK sleeps between retries outside those windows. It also
produces false failures: a larger per-read timeout can be a
perfectly good configuration under a deployment's own operational ceiling.
"""

import httpx
import pytest

from src.config import settings
from src.llm_client import OpenRouterClient


@pytest.fixture
def client():
    return OpenRouterClient(api_key="test-key")


def test_retry_count_reaches_the_sdk(client):
    """The knob is wired, not merely declared."""
    assert client._client.max_retries == settings.llm_max_retries


def test_retry_count_is_configurable(monkeypatch):
    """A deployment can shorten a stalled chain without editing source."""
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    assert OpenRouterClient(api_key="test-key")._client.max_retries == 0


def test_shipped_default_matches_the_sdk_default():
    """Installing this release must not change retry behaviour by itself.

    Shipping anything other than the SDK's own value would silently trade a
    recovery attempt for a shorter stall on every existing deployment — a
    resilience regression nobody opted into. Operators who want the shorter stall
    set RESEARCH_LLM_MAX_RETRIES themselves.

    Read from the SDK rather than hardcoded: `openai` is an open dependency range,
    so a hardcoded literal would prove only that our default is 2, not that it
    still matches the installed SDK if upstream ever moves.
    """
    from openai._constants import DEFAULT_MAX_RETRIES

    assert settings.llm_max_retries == DEFAULT_MAX_RETRIES


def test_connect_timeout_stays_short(client):
    """A dead endpoint should fail fast rather than burn the read budget."""
    assert client._client.timeout.connect <= 30.0


def test_request_timeout_comes_from_settings(client):
    assert client._client.timeout.read == float(settings.llm_timeout)


def test_endpoint_and_model_come_from_settings(monkeypatch):
    """The selected endpoint and model must be configuration, never literals.

    A hardcoded base_url or model would still pass every timeout assertion above
    while silently pinning the deployment to one provider.
    """
    monkeypatch.setattr(settings, "llm_api_base", "https://sentinel.invalid/v1")
    monkeypatch.setattr(settings, "llm_model", "sentinel/model")
    c = OpenRouterClient(api_key="test-key")
    assert str(c._client.base_url).rstrip("/") == "https://sentinel.invalid/v1"
    assert c.model == "sentinel/model"


def test_timeout_is_per_operation_not_wall_clock(client):
    """Guards the misreading this module's docstring exists to prevent.

    `httpx.Timeout` carries separate connect/read/write/pool values; none of them
    is a total-call deadline. A test that treated `timeout.read` as one would be
    asserting something httpx does not provide.
    """
    t = client._client.timeout
    assert isinstance(t, httpx.Timeout)
    assert t.connect != t.read, (
        "connect and read are distinct operation timeouts; if these are ever "
        "equal by construction, re-read whether the value is being treated as a "
        "wall-clock bound"
    )
