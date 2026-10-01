"""SearXNG vertical lanes.

SearXNG groups engines into categories. The bundled companion configuration
(`companions/searxng/settings.yml.example`) uses:

- general: DuckDuckGo, Brave, Wikipedia, Wikidata
- science: arXiv, Google Scholar, Semantic Scholar, OpenAlex, PubMed, Crossref
- it: Stack Overflow, Ask Ubuntu, Super User, GitHub, Hacker News
- videos: YouTube
- docs (opt-in): MDN, Microsoft Learn, Arch Linux wiki
- packages (opt-in): PyPI, npm, crates.io, pkg.go.dev, Docker Hub, Hugging Face

`science`, `it` and `videos` are stock SearXNG categories. `docs` and
`packages` exist only where the instance defines them, as the companion does.
A lane runs only when the instance's `/config` shows an enabled engine in its
category (see `connectors/searxng.py`).

The fused search always queries `general`. A vertical lane adds ONE more
SearXNG list from a single category when the caller's focus mode asks for it,
or, with no focus mode (or `comparison` / `news`), when a conservative keyword
heuristic matches. An explicit `general` focus means the general lane only.
Most vertical engines are API-backed, so they keep answering when the scraped
general engines are CAPTCHA'd. `docs` and `packages` are never auto-selected:
they keyword-match across languages and ecosystems, so they are reached only
through an explicit vertical request.
"""

import re

VERTICALS: tuple[str, ...] = ("general", "science", "it", "videos", "docs", "packages")

# Categories the automatic routing may add as a second lane.
AUTO_VERTICALS: tuple[str, ...] = ("science", "it", "videos")

# Focus modes with a fixed lane. `general` means the base lane only.
FOCUS_TO_VERTICAL: dict[str, str | None] = {
    "general": None,
    "academic": "science",
    "documentation": "it",
    "debugging": "it",
    "tutorial": "videos",
}

# Focus modes with no natural category: the keyword heuristic decides.
HEURISTIC_FOCUS_MODES: frozenset[str] = frozenset({"comparison", "news"})

# Checked in this order; the first match wins, so a debugging question phrased
# "how to fix ..." routes to `it`, not `videos`.
_HEURISTICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("science", re.compile(
        r"\b(papers?|arxiv|preprints?|peer[- ]reviewed|journals?|meta[- ]analys[ie]s"
        r"|systematic review|literature review|clinical trials?|pubmed|doi)\b",
        re.IGNORECASE,
    )),
    ("it", re.compile(
        r"\b(error|exceptions?|traceback|stack ?trace|segfault|stack ?overflow"
        r"|github issue|bug in|crash(es|ing)?|not working|fails? to)\b",
        re.IGNORECASE,
    )),
    ("videos", re.compile(
        r"\b(videos?|youtube|tutorials?|walkthrough|screencast|webinar|keynote"
        r"|conference talk|how[- ]to)\b",
        re.IGNORECASE,
    )),
)


def classify_vertical(query: str) -> str | None:
    """Pick an auto vertical for a query from keyword cues, or None."""
    for vertical, pattern in _HEURISTICS:
        if pattern.search(query):
            return vertical
    return None


def resolve_vertical(query: str, vertical: str | None) -> str | None:
    """Resolve an aggregator vertical setting to the lane for one query.

    Args:
        query: The query about to be searched.
        vertical: None (no vertical lane), "auto" (keyword heuristic), a focus
            mode name (`general` = no lane; `comparison` / `news` = heuristic),
            or a vertical name.

    Returns:
        A vertical category other than `general`, or None.
    """
    if not vertical:
        return None
    if vertical == "auto" or vertical in HEURISTIC_FOCUS_MODES:
        return classify_vertical(query)
    if vertical in FOCUS_TO_VERTICAL:
        return FOCUS_TO_VERTICAL[vertical]
    if vertical in VERTICALS and vertical != "general":
        return vertical
    return None
