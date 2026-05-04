"""LLM client for OpenRouter.

Simple AsyncOpenAI wrapper for OpenRouter with per-request API key support.
"""

import logging
import httpx
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI
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
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=timeout,
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
        response = await self._client.chat.completions.create(
            model=current_model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            **kwargs,
        )
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
