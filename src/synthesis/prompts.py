"""Prompts for research synthesis with citation support."""

from ..connectors.base import Source


RESEARCH_SYSTEM_PROMPT = """You are a research synthesis assistant. Your task is to analyze search results and provide comprehensive, well-cited answers.

CITATION RULES:
1. Use inline citations in the format [source_id] where source_id matches the provided sources
2. Every factual claim MUST have a citation
3. Cite the most relevant source for each claim
4. You may cite multiple sources for a single claim: [id1][id2]
5. Place citations immediately after the relevant statement

RESPONSE FORMAT:
1. Provide a clear, comprehensive answer to the query
2. Structure with headings if the topic is complex
3. Include all relevant citations inline
4. End with a "Sources" section listing all cited sources

QUALITY REQUIREMENTS:
- Be thorough but concise
- Synthesize information across sources, don't just summarize each
- Identify agreements and disagreements between sources
- Note when information is limited or uncertain
- Prioritize recent and authoritative sources"""


def build_research_prompt(query: str, sources: list[Source]) -> str:
    """
    Build the research prompt with sources for LLM synthesis.

    Args:
        query: User's research query
        sources: List of sources from search aggregation

    Returns:
        Formatted prompt with sources context
    """
    sources_text = "\n\n".join([
        f"[{s.id}] {s.title}\nURL: {s.url}\nContent: {s.content}"
        for s in sources
    ])

    return f"""Research Query: {query}

Available Sources:
{sources_text}

Please synthesize a comprehensive answer to the research query using the sources above. Remember to cite sources using [source_id] format."""


def format_citations(sources: list[Source]) -> str:
    """Format sources as a citation list."""
    return "\n".join([
        f"[{s.id}] {s.title} - {s.url}"
        for s in sources
    ])
