"""LLM client for OpenRouter.

Simple AsyncOpenAI wrapper for OpenRouter with per-request API key support.
"""

import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI
from . import progress
from .config import settings

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """AsyncOpenAI wrapper for OpenRouter with per-request API key support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Initialize OpenRouter client.

        Args:
            api_key: OpenRouter API key (defaults to settings)
            base_url: API base URL (defaults to settings)
            model: Model to use (defaults to settings)
        """
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_api_base
        self.model = model or settings.llm_model

        # Configure timeout
        timeout = httpx.Timeout(
            timeout=settings.llm_timeout,
            connect=10.0,
        )
        # Construct the client even when no key is configured. The OpenAI SDK
        # raises `OpenAIError: Missing credentials` at CONSTRUCTION on an empty
        # key, which makes every object that owns a client un-constructible —
        # so a fresh clone with no RESEARCH_LLM_API_KEY could not even run its
        # own unit tests (`SynthesisEngine()` raised before asserting a single
        # attribute). Construction is not the right guard: real usage is gated
        # by `settings.require_llm_key()` at the MCP and REST entrypoints, and
        # a request made with this placeholder fails with a normal upstream 401.
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "not-configured",
            timeout=timeout,
            # (llm_max_retries + 1) attempts. The SDK retries read-timeouts the
            # same way it retries 429s, so this multiplies `llm_timeout`: one
            # stalled upstream connection costs that product in silent retrying,
            # which is how a chain outlives an MCP client's abort deadline while
            # the server never gets to report a failure. Defaults to the SDK's
            # own value, so behaviour is unchanged unless a deployment sets it.
            max_retries=settings.llm_max_retries,
        )

        # Track which model was last used
        self.last_model_used: Optional[str] = None

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Any:
        """Create chat completion.

        Args:
            messages: Chat messages
            model: Override model
            temperature: Generation temperature
            top_p: Top-p sampling
            max_tokens: Max output tokens
            **kwargs: Additional parameters passed to API

        Returns:
            OpenAI ChatCompletion response
        """
        current_model = model or self.model
        temperature = temperature if temperature is not None else settings.llm_temperature
        top_p = top_p if top_p is not None else settings.llm_top_p
        max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens

        logger.debug(f"Request with model: {current_model}")

        # Every LLM call this server makes funnels through here — MCP tools, REST
        # routes, the synthesis engine and library callers all reach the SDK via
        # this method — so it is the one place that can bound and report on all
        # of them. See `src/progress.py` for why silence is the problem.
        #
        # The nesting is load-bearing, not stylistic:
        #   * the heartbeat is cancelled in an INNER `finally`, before settlement,
        #     so no heartbeat can land after "returned"/"failed" and break the
        #     monotonic ordering MCP requires;
        #   * cleanup uses `asyncio.gather(..., return_exceptions=True)` rather
        #     than `try: await hb; except CancelledError: pass` — the latter also
        #     swallows a cancellation delivered to THIS task while it waits, and
        #     the request would then carry on to settlement as if nothing had
        #     happened;
        #   * settlement lives in `except Exception` / `else`, never `finally`, so
        #     an external `CancelledError` (a BaseException) matches neither and
        #     performs no transport I/O — cancellation is neither delayed nor
        #     masked;
        #   * a FAILED call reports too. Reporting only on success left a hole: a
        #     failed chain gets swallowed by a broad `except Exception` upstream
        #     and is followed by another silent call.
        await progress.tick("model call started")
        heartbeat = progress.start_heartbeat()
        cap = settings.llm_wall_clock_cap
        try:
            try:
                async with asyncio.timeout(cap if cap > 0 else None):
                    response = await self._client.chat.completions.create(
                        model=current_model,
                        messages=messages,
                        temperature=temperature,
                        top_p=top_p,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
        except Exception:
            await progress.tick("model call failed")
            raise
        else:
            await progress.tick("model call returned")
            self.last_model_used = current_model
            return response

    @property
    def chat(self):
        """Compatibility property for code expecting client.chat.completions pattern."""
        return _ChatNamespace(self)


class _ChatNamespace:
    """Namespace for chat.completions compatibility."""

    def __init__(self, client: OpenRouterClient):
        self._client = client
        self.completions = _CompletionsNamespace(client)


class _CompletionsNamespace:
    """Namespace for chat.completions.create() compatibility."""

    def __init__(self, client: OpenRouterClient):
        self._client = client

    async def create(
        self,
        model: str,
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> Any:
        """Create chat completion."""
        return await self._client.chat_completion(
            messages=messages,
            model=model,
            **kwargs,
        )


def get_llm_client(api_key: str | None = None) -> OpenRouterClient:
    """Get OpenRouter client.

    Args:
        api_key: Optional per-request API key. Uses server default if None.
    """
    return OpenRouterClient(api_key=api_key if api_key else None)
