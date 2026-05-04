#!/usr/bin/env python3
"""Exa /answer MCP wrapper — fast factual lookups for agent mid-task operations."""

import os
import json
import sys
import urllib.request
import urllib.error

_original_print = print
def print(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    _original_print(*args, **kwargs)

from mcp.server.fastmcp import FastMCP

EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
EXA_API_URL = "https://api.exa.ai/answer"

if not EXA_API_KEY:
    print("ERROR: EXA_API_KEY must be set in the environment.", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("exa-answer", instructions=(
    "Fast factual answers with citations via Exa's neural search. "
    "Use for mid-task factual lookups where speed matters (1-2s, 94% SimpleQA accuracy). "
    "Do NOT use for exploratory research or deep analysis — use research-workflow for that."
))


def _call_answer(query: str, text: bool = True, model: str | None = None,
                 system_prompt: str | None = None) -> dict:
    """Call Exa /answer endpoint."""
    body: dict = {"query": query, "text": text}
    if model:
        body["model"] = model
    if system_prompt:
        body["system_prompt"] = system_prompt

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(EXA_API_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "exa-answer-mcp/1.0")
    req.add_header("x-api-key", EXA_API_KEY)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        return {"error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def exa_answer(query: str, include_sources: bool = True) -> str:
    """Fast factual answer with citations. 1-2s, 94% SimpleQA accuracy.

    Use for mid-task factual lookups where speed matters more than depth.
    Exa searches its neural index and returns a direct answer with citations.

    Do NOT use for exploratory research — use research-workflow for that.

    Args:
        query: The question to answer. Be specific for best results.
        include_sources: Include source URLs in the response (default True).

    Returns:
        Direct answer with optional source citations.
    """
    result = _call_answer(query, text=False)

    if "error" in result:
        return f"Error: {result['error']}"

    answer = result.get("answer", "No answer generated.")
    output = answer

    if include_sources and "citations" in result:
        citations = result["citations"]
        if citations:
            output += "\n\nSources:"
            for c in citations:
                title = c.get("title", "")
                url = c.get("url", "")
                if url:
                    output += f"\n- [{title}]({url})" if title else f"\n- {url}"

    return output


@mcp.tool()
def exa_answer_detailed(query: str, system_prompt: str = "") -> str:
    """Detailed factual answer with full source text. For when you need more context.

    Like exa_answer but includes the full text of source pages in the response.
    Use when you need to verify claims or extract additional details from sources.

    Args:
        query: The question to answer.
        system_prompt: Optional system prompt to guide the answer style.

    Returns:
        Detailed answer with full source content.
    """
    result = _call_answer(query, text=True, system_prompt=system_prompt or None)

    if "error" in result:
        return f"Error: {result['error']}"

    answer = result.get("answer", "No answer generated.")
    output = answer

    citations = result.get("citations", [])
    if citations:
        output += "\n\n---\n\nSources:"
        for i, c in enumerate(citations, 1):
            title = c.get("title", "Untitled")
            url = c.get("url", "")
            text = c.get("text", "")
            output += f"\n\n### [{i}] {title}"
            if url:
                output += f"\n{url}"
            if text:
                if len(text) > 2000:
                    text = text[:2000] + "\n...(truncated)"
                output += f"\n{text}"

    cost = result.get("costDollars", {}).get("total")
    if cost is not None:
        output += f"\n\n---\nCost: ${cost:.4f}"

    return output


if __name__ == "__main__":
    mcp.run(transport="stdio")
