"""LLM utility functions.

Handles compatibility with reasoning models (DeepSeek-R1, Tongyi-DeepResearch)
that output to `reasoning_content` or `reasoning` instead of `content`.

OpenRouter returns reasoning in the `reasoning` field (not `reasoning_content`
used by native DeepSeek API). Both are checked for portability.
"""


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
