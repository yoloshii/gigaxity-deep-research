"""Prompts for research synthesis with `[N]` citation support.

v0.3.0 unified the `SynthesisEngine` path onto the same numeric citation
contract as `SynthesisAggregator` and `OutlineGuidedSynthesizer` (codex DESIGN
session 019e39f7, Q1 hard cutover + Q3 reuse `CITATION_FORMAT_GUIDE`). The
old `[source_id]` contract is gone — both the rules block and the source
listing now speak `[N]`, so the model sees one internally-consistent format.
"""

from ..connectors.base import Source
from .citations import CITATION_FORMAT_GUIDE


RESEARCH_SYSTEM_PROMPT = f"""You are a research synthesis assistant. Your task is to analyze search results and provide comprehensive, well-cited answers.

{CITATION_FORMAT_GUIDE}

CITATION DISCIPLINE:
1. Every factual claim MUST have a citation.
2. Cite the most relevant source for each claim.
3. Place the citation immediately after the relevant statement.
4. You may stack markers for co-citation: `[1][3]`.

RESPONSE FORMAT:
1. Provide a clear, comprehensive answer to the query.
2. Structure with headings if the topic is complex.
3. Include all relevant citations inline.
4. End with a "Sources" section listing all cited sources.

QUALITY REQUIREMENTS:
- Be thorough but concise.
- Synthesize information across sources; don't just summarize each.
- Identify agreements and disagreements between sources.
- Note when information is limited or uncertain.
- Prioritize recent and authoritative sources.

OUTPUT FORMAT:
- Wrap your entire answer in <answer> and </answer> tags.
- Put NOTHING after the closing </answer> tag — no changelog, no notes about your own process (no "corrections made", "changes implemented", or "editorial notes")."""


def build_research_prompt(query: str, sources: list[Source]) -> str:
    """Build the research prompt with sources rendered as `[N]` blocks.

    Source ordering is load-bearing — the model is asked to cite `[1]` for
    `sources[0]`, `[2]` for `sources[1]`, etc. `extract_numeric_citations()`
    in `citations.py` resolves the same 1-based mapping on the way back out.
    """
    sources_text = "\n\n".join([
        f"[{i + 1}] {s.title}\nURL: {s.url}\nContent: {s.content}"
        for i, s in enumerate(sources)
    ])

    return f"""Research Query: {query}

Available Sources:
{sources_text}

Please synthesize a comprehensive answer to the research query using the sources above. Cite sources using `[N]` markers (1-based) matching the source list."""


def format_citations(sources: list[Source]) -> str:
    """Format sources as a numeric citation list (`[1] title - url`)."""
    return "\n".join([
        f"[{i + 1}] {s.title} - {s.url}"
        for i, s in enumerate(sources)
    ])
