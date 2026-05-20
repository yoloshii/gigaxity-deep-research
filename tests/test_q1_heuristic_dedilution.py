"""Q1: de-diluted keyword heuristic.

Replaces `matches / len(query_terms)` (which collapsed every score as the query
grew) with a length-independent saturating function of the DISTINCT
content-bearing query terms a source matches at a token boundary, over title +
content. Reused on the degraded fallback path, so its scores must stay
meaningful (codex design 019e4569). Assertions per the codex T4 test note: prove
dilution is gone, avg stays above reject, 3+ matches clear pass; do NOT assert a
blanket "every source passes" (2 matches ~ 0.393 is below the 0.4 comp pass).
"""

import math
from types import SimpleNamespace

from src.synthesis.quality_gate import SourceQualityGate


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


def _score(query, source):
    # heuristic_only path: construct without llm_client, score directly.
    return SourceQualityGate()._score_sources_heuristic(query, [source])[0]


def test_dilution_gone_length_independent():
    """A verbose brief must NOT collapse a relevant source's score.

    Same source + same core terms, a short query vs a 34-extra-term brief: the
    score depends on matched core terms, not on query length.
    """
    src = _src("Agentic runtime sandbox", "agentic runtime security sandbox guardrails egress")
    short_q = "agentic runtime sandbox"
    long_q = "agentic runtime sandbox " + " ".join(f"unrelated{i}" for i in range(34))
    short_score = _score(short_q, src)
    long_score = _score(long_q, src)
    assert short_score == long_score          # length-independent
    assert short_score >= 0.5                 # 3 core matches → ~0.528, above reject


def test_three_core_matches_clear_comprehensive_pass():
    """3 distinct matched terms ~ 0.528 >= comprehensive pass 0.4."""
    s = _score("alpha beta gamma", _src("T", "alpha beta gamma"))
    assert math.isclose(s, 1.0 - math.exp(-0.75), rel_tol=1e-9)
    assert s >= 0.4


def test_one_or_two_matches_below_comprehensive_pass():
    """1-2 matches stay below the 0.4 comprehensive pass (correctly not promoted)."""
    one = _score("alpha unmatchedx unmatchedy", _src("T", "alpha only"))
    two = _score("alpha beta unmatchedz", _src("T", "alpha beta"))
    assert one < 0.4   # ~0.221
    assert two < 0.4   # ~0.393


def test_stopwords_excluded_from_term_count():
    """Function words must not count as matched terms (Q1a)."""
    q = "kubernetes between without through about which"
    src = _src("T", "kubernetes between without through about which")
    # Only "kubernetes" is content-bearing → 1 match, NOT ~6.
    assert math.isclose(_score(q, src), 1.0 - math.exp(-0.25), rel_tol=1e-9)


def test_token_boundary_not_substring():
    """'research' must not match 'researcher' (Q1b boundary)."""
    assert _score("research", _src("T", "research findings")) > 0.0
    assert _score("research", _src("T", "the researcher said")) == 0.0


def test_title_counts_toward_match():
    """A match in the title counts, not just content (Q1c)."""
    assert _score("kubernetes", _src("Kubernetes operators", "unrelated body")) > 0.0


def test_all_stopword_query_returns_neutral():
    """A query with no content-bearing terms → neutral 0.5 (unchanged behavior)."""
    assert _score("with that this from", _src("T", "with that this from")) == 0.5
