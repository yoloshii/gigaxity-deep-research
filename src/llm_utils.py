"""LLM utility functions.

Handles compatibility with reasoning models (DeepSeek-R1, Tongyi-DeepResearch)
that output to `reasoning_content` or `reasoning` instead of `content`.

OpenRouter returns reasoning in the `reasoning` field (not `reasoning_content`
used by native DeepSeek API). Both are checked for portability.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import settings


def get_llm_content(message) -> str:
    """
    Extract content from LLM response message.

    Reasoning models output chain-of-thought to a separate field and may
    leave `content` empty for structured/short prompts.

    Fallback chain: content → reasoning_content → reasoning

    Args:
        message: OpenAI-compatible message object with content attribute

    Returns:
        Content string, falling back to reasoning fields if content is empty
    """
    # Try standard content field first
    content = getattr(message, 'content', None) or ""

    if not content:
        # Native DeepSeek API field name
        content = getattr(message, 'reasoning_content', None) or ""

    if not content:
        # OpenRouter field name
        content = getattr(message, 'reasoning', None) or ""

    return content


class ExtractionMode(str, Enum):
    """How an LLM response should be treated when extracting LLMOutput.

    FINAL_ANSWER   - the caller needs a real answer in the `content` field.
                     A reasoning-field trace does not count as an answer
                     (reasoning_only is a failure for this mode).
    PARSE_REQUIRED - the caller will parse the text into a structure and
                     needs a valid parse; on parse failure the caller falls
                     back deterministically rather than using raw text.
    LENIENT        - any available text is acceptable, including a reasoning
                     trace. Used where the reasoning IS effectively the output.
    """
    FINAL_ANSWER = "final_answer"
    PARSE_REQUIRED = "parse_required"
    LENIENT = "lenient"


@dataclass
class LLMOutput:
    """Text extracted from an LLM response choice, with provenance.

    Carries enough signal for a caller to tell a real answer apart from a
    chain-of-thought trace or a truncated generation.
    """
    text: str                     # extracted text (answer, or reasoning trace in LENIENT mode)
    source_field: str             # "content" | "reasoning_content" | "reasoning" | "" (empty response)
    finish_reason: Optional[str]  # OpenAI finish_reason: "stop" | "length" | "content_filter" | None
    truncated: bool               # finish_reason == "length"
    reasoning_only: bool          # `content` was empty; text (if any) came from a reasoning field
    subcall_failed: bool = False  # combined outputs only: a contributing sub-call had no usable answer


# Model context windows (total tokens: prompt + completion). Used by the
# budget-aware source formatter to bound prompt size. Verify against the
# provider when adding a model; unknown models fall back to DEFAULT_CONTEXT_WINDOW.
DEFAULT_CONTEXT_WINDOW = 32768

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Same model, two id spellings across deployment targets: the OpenRouter
    # slug (main branch) and the HuggingFace path that vLLM/SGLang load by
    # default (local-inference branch). Both are Tongyi-DeepResearch-30B-A3B,
    # 131072-token window.
    "alibaba/tongyi-deepresearch-30b-a3b": 131072,
    "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B": 131072,
}


def get_context_window(model: str) -> int:
    """Return the context window for a model, or DEFAULT_CONTEXT_WINDOW if unknown."""
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


# Substrings that identify reasoning models - models that emit chain-of-thought
# (often to a separate `reasoning` field) and so need extra output-token
# headroom to both reason and produce a final answer.
_REASONING_MODEL_MARKERS = (
    "deepresearch",
    "deepseek-r1",
    "deepseek-reasoner",
    "-r1",
    "qwq",
    "reasoning",
    "thinking",
)


def is_reasoning_model(model: str) -> bool:
    """Heuristic: does this model emit chain-of-thought before its answer?

    False negatives are safe (model gets the base budget, the prior behavior);
    false positives are safe (model gets a higher max_tokens ceiling it simply
    will not use).
    """
    m = (model or "").lower()
    return any(marker in m for marker in _REASONING_MODEL_MARKERS)


def derive_effective_budget(base: int, model: str) -> int:
    """Effective output-token budget for a final-synthesis call.

    Reasoning models spend output tokens on chain-of-thought before the
    answer; `base` alone (e.g. 3000) gets consumed by reasoning and the answer
    never lands. For reasoning models, add the configured headroom. Capped at
    settings.llm_max_tokens so it never exceeds the provider ceiling.

    Args:
        base: the answer-budget base (e.g. a schema max_tokens default).
        model: the model id the call will use.

    Returns:
        min(base, llm_max_tokens) for non-reasoning models;
        min(base + llm_reasoning_headroom, llm_max_tokens) for reasoning models.
    """
    headroom = settings.llm_reasoning_headroom if is_reasoning_model(model) else 0
    return min(base + headroom, settings.llm_max_tokens)


def extract_llm_output(choice, mode: ExtractionMode) -> LLMOutput:
    """Extract usable text from an LLM response choice, honoring the mode.

    Reasoning models emit chain-of-thought to a separate field and may leave
    `content` empty. Falling back to a reasoning field is allowed only for
    LENIENT callers, where the reasoning IS effectively the output. For
    FINAL_ANSWER and PARSE_REQUIRED callers a reasoning trace is not usable -
    it is neither a final answer nor a valid parse source - so they get
    text="" and reasoning_only=True, and can fail fast (FINAL_ANSWER) or fall
    back deterministically (PARSE_REQUIRED) instead of treating the trace as
    real output.

    PARSE_REQUIRED additionally rejects TRUNCATED content (finish_reason ==
    "length"): a structured response cut short is not a valid parse source
    even if the fragment happens to look complete, so it too returns text=""
    and the caller falls back. FINAL_ANSWER keeps truncated content
    (call_with_extraction retries once; a still-truncated result is hard-gated
    by the verifier); LENIENT keeps whatever it received.

    Args:
        choice: an OpenAI-compatible response choice (e.g. response.choices[0]),
            or None.
        mode: how the caller intends to use the result.

    Returns:
        LLMOutput with the extracted text and provenance/truncation signals.
    """
    message = getattr(choice, "message", None)
    finish_reason = getattr(choice, "finish_reason", None)
    truncated = finish_reason == "length"

    content = getattr(message, "content", None) or ""
    reasoning_content = getattr(message, "reasoning_content", None) or ""
    reasoning = getattr(message, "reasoning", None) or ""

    if content:
        if mode == ExtractionMode.PARSE_REQUIRED and truncated:
            # A truncated structured response is not a valid parse source even
            # when the partial content happens to look complete (e.g. it has
            # the expected number of scores). PARSE_REQUIRED callers fall back
            # deterministically rather than parse a fragment. FINAL_ANSWER
            # keeps the partial content (call_with_extraction retries it once,
            # then the verifier hard-gates a still-truncated result); LENIENT
            # keeps whatever it received.
            return LLMOutput(
                text="",
                source_field="content",
                finish_reason=finish_reason,
                truncated=truncated,
                reasoning_only=False,
            )
        return LLMOutput(
            text=content,
            source_field="content",
            finish_reason=finish_reason,
            truncated=truncated,
            reasoning_only=False,
        )

    # `content` is empty - only a reasoning field (if any) carries text.
    reasoning_text = reasoning_content or reasoning
    reasoning_field = (
        "reasoning_content" if reasoning_content
        else "reasoning" if reasoning
        else ""
    )

    if mode != ExtractionMode.LENIENT:
        # FINAL_ANSWER and PARSE_REQUIRED both need a real answer in `content`.
        # A reasoning-field trace is neither a final answer nor a valid parse
        # source - returning it as text would let a chain-of-thought trace
        # masquerade as a structured answer. Surface the failure (text="") so
        # the caller fails fast (FINAL_ANSWER) or hits its deterministic
        # fallback (PARSE_REQUIRED). Only LENIENT, where the reasoning IS the
        # output, falls through to the reasoning text below.
        return LLMOutput(
            text="",
            source_field=reasoning_field,
            finish_reason=finish_reason,
            truncated=truncated,
            reasoning_only=bool(reasoning_text),
        )

    return LLMOutput(
        text=reasoning_text,
        source_field=reasoning_field,
        finish_reason=finish_reason,
        truncated=truncated,
        reasoning_only=bool(reasoning_text),
    )


async def call_with_extraction(
    client,
    model: str,
    messages: list[dict],
    max_tokens: int,
    mode: ExtractionMode,
    *,
    temperature: float = 0.7,
    top_p: Optional[float] = None,
) -> LLMOutput:
    """Make a chat completion and extract an LLMOutput, honoring the mode.

    For FINAL_ANSWER mode, if the first attempt is truncated by the token limit
    (finish_reason == "length") and max_tokens is below the configured ceiling
    (settings.llm_max_tokens), retry once at the ceiling. Other modes do not
    retry - their callers handle a short or unparseable response themselves.
    """
    create_kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if top_p is not None:
        create_kwargs["top_p"] = top_p

    response = await client.chat.completions.create(**create_kwargs)
    choice = response.choices[0] if getattr(response, "choices", None) else None
    output = extract_llm_output(choice, mode)

    if (
        mode == ExtractionMode.FINAL_ANSWER
        and output.truncated
        and max_tokens < settings.llm_max_tokens
    ):
        create_kwargs["max_tokens"] = settings.llm_max_tokens
        response = await client.chat.completions.create(**create_kwargs)
        choice = response.choices[0] if getattr(response, "choices", None) else None
        output = extract_llm_output(choice, mode)

    return output


def combine_llm_outputs(
    final_text: str,
    outputs: list[LLMOutput],
) -> Optional[LLMOutput]:
    """Summarize several LLMOutputs into one signal for an assembled result.

    A multi-stage synthesis (per-section drafting, refinement) has no single
    originating call. `truncated` and `reasoning_only` describe the aggregate
    (any truncated; reasoning-only only if every contributing call was).
    `subcall_failed` is the weakest-link signal: True if ANY contributing call
    was empty, reasoning-only, or truncated - so the verifier hard-gates an
    assembled result that has a failed section instead of passing it because
    the *other* sections succeeded. Only outputs that actually contributed to
    `final_text` should be passed in. Returns None when there are no
    contributing outputs.
    """
    outputs = [o for o in outputs if o is not None]
    if not outputs:
        return None
    truncated = any(o.truncated for o in outputs)
    reasoning_only = all(o.reasoning_only for o in outputs)
    subcall_failed = any(
        o.truncated or o.reasoning_only or not (o.text and o.text.strip())
        for o in outputs
    )
    return LLMOutput(
        text=final_text,
        source_field="content",
        finish_reason="length" if truncated else "stop",
        truncated=truncated,
        reasoning_only=reasoning_only,
        subcall_failed=subcall_failed,
    )
