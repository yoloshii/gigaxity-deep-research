"""Synthesis engine using Tongyi DeepResearch via OpenAI-compatible API."""

import re
from openai import AsyncOpenAI
from ..connectors.base import Source
from ..config import settings
from ..llm_utils import ExtractionMode, extract_llm_output
from ..llm_client import OpenRouterClient, get_llm_client
from .prompts import RESEARCH_SYSTEM_PROMPT, build_research_prompt, format_citations


class SynthesisEngine:
    """LLM-powered research synthesis with citation support."""

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        client: OpenRouterClient | None = None,
    ):
        """
        Initialize synthesis engine.

        Args:
            api_base: OpenAI-compatible API base URL
            api_key: API key (can be dummy for local models)
            model: Model name
            temperature: Generation temperature
            top_p: Top-p sampling parameter
            max_tokens: Maximum output tokens
            client: Optional OpenRouterClient for per-request API key support
        """
        self.api_base = api_base or settings.llm_api_base
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        self.top_p = top_p if top_p is not None else settings.llm_top_p
        self.max_tokens = max_tokens or settings.llm_max_tokens

        # Honor api_key + api_base overrides when constructing the LLM client.
        # The optional `client` argument still wins (callers can fully replace
        # the client). This pattern lets the planned local-inference branch be
        # a thin diff: pass api_base="http://localhost:8000/v1" and the engine
        # routes through the local OpenAI-compatible endpoint.
        if client is not None:
            self.client = client
        else:
            self.client = get_llm_client(api_key=self.api_key)
            if api_base is not None:
                self.client.base_url = self.api_base
                self.client._client.base_url = self.api_base

    async def synthesize(
        self,
        query: str,
        sources: list[Source],
        system_prompt: str | None = None,
    ) -> dict:
        """
        Synthesize research answer from sources.

        Args:
            query: Research query
            sources: List of sources to synthesize from
            system_prompt: Optional custom system prompt

        Returns:
            Dict with 'content', 'citations', and 'sources_used'
        """
        if not sources:
            return {
                "content": "No sources available to synthesize from.",
                "citations": [],
                "sources_used": [],
            }

        system = system_prompt or RESEARCH_SYSTEM_PROMPT
        user_prompt = build_research_prompt(query, sources)

        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )
            output = extract_llm_output(
                response.choices[0] if getattr(response, "choices", None) else None,
                ExtractionMode.FINAL_ANSWER,
            )

            # FINAL_ANSWER: retry once at the ceiling if the answer was
            # truncated by the token limit and there is headroom.
            if output.truncated and self.max_tokens < settings.llm_max_tokens:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    max_tokens=settings.llm_max_tokens,
                )
                output = extract_llm_output(
                    response.choices[0] if getattr(response, "choices", None) else None,
                    ExtractionMode.FINAL_ANSWER,
                )

            content = output.text

            # FINAL_ANSWER fail-fast: an empty result (truly empty, or a
            # reasoning-only trace) is not a synthesis answer.
            if not content:
                return {
                    "content": "Synthesis produced no answer content.",
                    "citations": [],
                    "sources_used": [],
                    "error": "empty_synthesis",
                }

            # Extract cited source IDs from response
            cited_ids = set(re.findall(r'\[([a-z]{2}_[a-f0-9]+)\]', content))

            # Build citations list
            sources_by_id = {s.id: s for s in sources}
            citations = []
            sources_used = []

            for sid in cited_ids:
                if sid in sources_by_id:
                    source = sources_by_id[sid]
                    citations.append({
                        "id": source.id,
                        "title": source.title,
                        "url": source.url,
                    })
                    sources_used.append(source)

            # Get actual model used (accounts for fallback)
            actual_model = getattr(self.client, 'last_model_used', None) or self.model

            return {
                "content": content,
                "citations": citations,
                "sources_used": sources_used,
                "model": actual_model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                },
            }

        except Exception as e:
            return {
                "content": f"Synthesis error: {e}",
                "citations": [],
                "sources_used": [],
                "error": str(e),
            }

    async def research(
        self,
        query: str,
        sources: list[Source],
        reasoning_effort: str = "medium",
    ) -> dict:
        """
        Perform deep research synthesis over multi-source content.

        Args:
            query: Research query
            sources: Sources to analyze
            reasoning_effort: "low", "medium", or "high" (affects prompt depth)

        Returns:
            Research result with synthesis and citations
        """
        # Adjust system prompt based on reasoning effort
        effort_prompts = {
            "low": RESEARCH_SYSTEM_PROMPT,
            "medium": RESEARCH_SYSTEM_PROMPT + "\n\nProvide a balanced analysis with key findings.",
            "high": RESEARCH_SYSTEM_PROMPT + """

DEEP ANALYSIS REQUIREMENTS:
- Perform exhaustive analysis of all sources
- Identify patterns, trends, and contradictions
- Provide nuanced interpretation of findings
- Consider multiple perspectives and edge cases
- Draw well-supported conclusions
- Suggest areas for further research if applicable""",
        }

        system = effort_prompts.get(reasoning_effort, effort_prompts["medium"])
        return await self.synthesize(query, sources, system)
