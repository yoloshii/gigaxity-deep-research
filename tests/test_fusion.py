"""Tests for RRF fusion algorithm."""

import pytest
from src.search.fusion import rrf_fusion
from src.connectors.base import Source


class TestRRFFusion:
    """Tests for Reciprocal Rank Fusion."""

    @pytest.mark.unit
    def test_empty_input(self):
        """Empty input returns empty output."""
        result = rrf_fusion([])
        assert result == []

    @pytest.mark.unit
    def test_single_list(self):
        """Single list returns same order with RRF scores."""
        sources = [
            Source(id="a", title="A", url="http://a.com", content="A"),
            Source(id="b", title="B", url="http://b.com", content="B"),
        ]
        result = rrf_fusion([sources], k=60)

        assert len(result) == 2
        assert result[0].url == "http://a.com"
        assert result[1].url == "http://b.com"
        # RRF score for rank 1: 1/(60+1) ≈ 0.0164
        assert abs(result[0].score - 1/61) < 0.0001

    @pytest.mark.unit
    def test_multiple_lists_boost(self):
        """Items appearing in multiple lists get boosted."""
        list1 = [
            Source(id="a1", title="A", url="http://a.com", content="A"),
            Source(id="b1", title="B", url="http://b.com", content="B"),
        ]
        list2 = [
            Source(id="b2", title="B", url="http://b.com", content="B"),  # Same URL as b1
            Source(id="c2", title="C", url="http://c.com", content="C"),
        ]

        result = rrf_fusion([list1, list2], k=60)

        # B appears in both lists, should be ranked higher
        urls = [s.url for s in result]
        assert urls[0] == "http://b.com"

    @pytest.mark.unit
    def test_deduplication_by_url(self):
        """Duplicate URLs are deduplicated."""
        list1 = [
            Source(id="a1", title="A1", url="http://same.com", content="Version 1"),
        ]
        list2 = [
            Source(id="a2", title="A2", url="http://same.com", content="Version 2"),
        ]

        result = rrf_fusion([list1, list2], k=60)

        assert len(result) == 1
        # First occurrence is kept
        assert result[0].id == "a1"

    @pytest.mark.unit
    def test_top_k_limit(self):
        """Results are limited to top_k."""
        sources = [
            Source(id=f"s{i}", title=f"S{i}", url=f"http://{i}.com", content=f"C{i}")
            for i in range(10)
        ]

        result = rrf_fusion([sources], k=60, top_k=3)
        assert len(result) == 3

    @pytest.mark.unit
    def test_k_parameter_effect(self):
        """Lower k gives more weight to top ranks."""
        # A appears at rank 1 in list1, rank 3 in list2
        # B appears at rank 1 in list2 only
        # C appears at rank 2 in list2 only

        # Create fresh sources for low-k test (rrf_fusion modifies scores in-place)
        list1_low = [
            Source(id="a", title="A", url="http://a.com", content="A"),
        ]
        list2_low = [
            Source(id="b", title="B", url="http://b.com", content="B"),
            Source(id="c", title="C", url="http://c.com", content="C"),
            Source(id="a2", title="A", url="http://a.com", content="A"),  # A at rank 3
        ]

        # Create fresh sources for high-k test
        list1_high = [
            Source(id="a", title="A", url="http://a.com", content="A"),
        ]
        list2_high = [
            Source(id="b", title="B", url="http://b.com", content="B"),
            Source(id="c", title="C", url="http://c.com", content="C"),
            Source(id="a2", title="A", url="http://a.com", content="A"),  # A at rank 3
        ]

        # With low k, rank difference matters more
        result_low_k = rrf_fusion([list1_low, list2_low], k=1)
        # With high k, rank difference matters less
        result_high_k = rrf_fusion([list1_high, list2_high], k=100)

        # Get scores for A (appears in both lists)
        a_score_low = next(s.score for s in result_low_k if s.url == "http://a.com")
        b_score_low = next(s.score for s in result_low_k if s.url == "http://b.com")
        a_score_high = next(s.score for s in result_high_k if s.url == "http://a.com")
        b_score_high = next(s.score for s in result_high_k if s.url == "http://b.com")

        # A's advantage from appearing in 2 lists should be relatively larger with high k
        # because the rank penalty is smaller proportionally
        ratio_low = a_score_low / b_score_low
        ratio_high = a_score_high / b_score_high
        # With high k, A's multi-list advantage becomes more pronounced
        assert ratio_high > ratio_low

    @pytest.mark.unit
    def test_score_calculation(self):
        """RRF scores are calculated correctly."""
        k = 60
        sources = [
            Source(id="a", title="A", url="http://a.com", content="A"),
            Source(id="b", title="B", url="http://b.com", content="B"),
            Source(id="c", title="C", url="http://c.com", content="C"),
        ]

        result = rrf_fusion([sources], k=k)

        expected_scores = [1/(k+1), 1/(k+2), 1/(k+3)]
        for i, source in enumerate(result):
            assert abs(source.score - expected_scores[i]) < 0.0001

    @pytest.mark.unit
    def test_three_lists_fusion(self):
        """Fusion works correctly with three lists."""
        list1 = [
            Source(id="a1", title="A", url="http://a.com", content="A"),
            Source(id="b1", title="B", url="http://b.com", content="B"),
        ]
        list2 = [
            Source(id="b2", title="B", url="http://b.com", content="B"),
            Source(id="c2", title="C", url="http://c.com", content="C"),
        ]
        list3 = [
            Source(id="b3", title="B", url="http://b.com", content="B"),
            Source(id="a3", title="A", url="http://a.com", content="A"),
        ]

        result = rrf_fusion([list1, list2, list3], k=60)

        # B appears in all 3 lists at rank 1, 1, 1
        # A appears in 2 lists at rank 1, 2
        # C appears in 1 list at rank 2
        urls = [s.url for s in result]
        assert urls[0] == "http://b.com"  # B should be first
        assert urls[1] == "http://a.com"  # A should be second
        assert urls[2] == "http://c.com"  # C should be third
