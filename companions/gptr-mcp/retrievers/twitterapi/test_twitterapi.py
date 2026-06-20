"""Bug-first tests for the native TwitterAPI.io X/Twitter retriever.

Run from the gpt-researcher repo root with the project venv:
    .venv/bin/python -m pytest gpt_researcher/retrievers/twitterapi/test_twitterapi.py
or, dependency-free:
    .venv/bin/python -m unittest gpt_researcher.retrievers.twitterapi.test_twitterapi

These assert CORRECT behavior (the safety contract). A regression that re-opens
a hole — a write endpoint becoming reachable, a result returned as a bare
scrapeable x.com href, a paid call escaping the budget, a cookie attached to a
request — fails the suite. NO test contacts a live network: TwitterAPI.io HTTP
is mocked at the `_http_get_json` / opener boundary.
"""

import os
import unittest
import urllib.error
from unittest import mock

from . import twitterapi as ta
from .twitterapi import (
    TwitterAPISearch,
    _BudgetGate,
    build_query,
    format_tweet,
    _http_get_json,
)


def _tweet(text="a substantive tweet about the topic " * 3, handle="alice",
           tid="1", **extra):
    d = {
        "text": text,
        "id": tid,
        "url": f"https://x.com/{handle}/status/{tid}",
        "author": {"userName": handle, "name": handle.title(),
                   "followers": 1234, "isBlueVerified": True},
        "likeCount": 10, "retweetCount": 2, "viewCount": 999,
        "createdAt": "Fri Jun 20 2026",
    }
    d.update(extra)
    return d


class TestQueryBuilder(unittest.TestCase):
    def test_core_query_and_default_retweet_filter(self):
        with mock.patch.object(ta, "TWITTERAPI_FILTER_RETWEETS", True), \
             mock.patch.object(ta, "TWITTERAPI_QUERY_LANG", ""), \
             mock.patch.object(ta, "TWITTERAPI_SINCE_TIME", 0), \
             mock.patch.object(ta, "TWITTERAPI_UNTIL_TIME", 0):
            q = build_query("AI agents")
        self.assertIn("AI agents", q)
        self.assertIn("-filter:retweets", q)

    def test_lang_and_epoch_time_operators(self):
        with mock.patch.object(ta, "TWITTERAPI_FILTER_RETWEETS", False), \
             mock.patch.object(ta, "TWITTERAPI_QUERY_LANG", "en"), \
             mock.patch.object(ta, "TWITTERAPI_SINCE_TIME", 1700000000), \
             mock.patch.object(ta, "TWITTERAPI_UNTIL_TIME", 1700100000):
            q = build_query("rust async")
        self.assertIn("lang:en", q)
        self.assertIn("since_time:1700000000", q)
        self.assertIn("until_time:1700100000", q)
        self.assertNotIn("-filter:retweets", q)
        # Never the unsupported calendar form.
        self.assertNotIn("since:", q.replace("since_time:", ""))

    def test_empty_core_query(self):
        with mock.patch.object(ta, "TWITTERAPI_FILTER_RETWEETS", False), \
             mock.patch.object(ta, "TWITTERAPI_QUERY_LANG", ""), \
             mock.patch.object(ta, "TWITTERAPI_SINCE_TIME", 0), \
             mock.patch.object(ta, "TWITTERAPI_UNTIL_TIME", 0):
            self.assertEqual(build_query("   "), "")

    def test_invalid_query_type_clamps(self):
        allowed = frozenset({"Top", "Latest"})
        with mock.patch.dict(os.environ, {"Q": "Garbage"}):
            self.assertEqual(ta._env_choice("Q", "Top", allowed), "Top")
        with mock.patch.dict(os.environ, {"Q": "Latest"}):
            self.assertEqual(ta._env_choice("Q", "Top", allowed), "Latest")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("Q", None)
            self.assertEqual(ta._env_choice("Q", "Top", allowed), "Top")
        # the module constants themselves are always within their allowed sets
        self.assertIn(ta.TWITTERAPI_QUERY_TYPE, {"Top", "Latest"})
        self.assertIn(ta.TWITTERAPI_REPLY_QUERY_TYPE, {"Relevance", "Latest", "Likes"})


class TestPathAllowlist(unittest.TestCase):
    """The write-endpoint safety boundary: host + path + no-cookie + no-redirect."""

    def test_refuses_non_https(self):
        with self.assertRaises(ValueError):
            _http_get_json("http://api.twitterapi.io/twitter/tweet/advanced_search", "k", 5)

    def test_refuses_off_host(self):
        with self.assertRaises(ValueError):
            _http_get_json("https://evil.com/twitter/tweet/advanced_search", "k", 5)
        # look-alike host must not pass
        with self.assertRaises(ValueError):
            _http_get_json("https://api.twitterapi.io.evil.com/twitter/tweet/advanced_search", "k", 5)

    def test_refuses_write_paths_before_socket(self):
        # A write/state path must raise BEFORE any network call is attempted.
        opener = mock.Mock()
        with mock.patch.object(ta, "_OPENER", opener):
            for path in (
                "/twitter/tweet/create", "/twitter/tweet/delete",
                "/twitter/like/create", "/twitter/follow/create",
                "/twitter/user/login_v2", "/twitter/dm/send",
            ):
                with self.assertRaises(ValueError, msg=path):
                    _http_get_json(f"https://api.twitterapi.io{path}", "k", 5)
        opener.open.assert_not_called()

    def test_allows_only_the_four_read_paths(self):
        captured = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'

        def _fake_open(req, timeout=None):
            captured["headers"] = dict(req.headers)
            captured["url"] = req.full_url
            return _Resp()

        with mock.patch.object(ta._OPENER, "open", side_effect=_fake_open):
            for path in sorted(ta._ALLOWED_PATHS):
                captured.clear()
                out = _http_get_json(f"https://api.twitterapi.io{path}?q=1", "secret-key", 5)
                self.assertEqual(out, {"ok": True})
                # X-API-Key is sent; NO cookie/login header ever is.
                hdrs = {k.lower(): v for k, v in captured["headers"].items()}
                self.assertEqual(hdrs.get("x-api-key"), "secret-key")
                self.assertNotIn("cookie", hdrs)
                self.assertFalse(any("cookie" in k for k in hdrs))

    def test_redirect_is_refused(self):
        import urllib.request
        h = ta._RefuseRedirect()
        req = urllib.request.Request("https://api.twitterapi.io/twitter/tweet/advanced_search")
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(req, None, 302, "Found", {}, "https://x.com/evil")


class TestBudgetGate(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ta.time, "sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)

    def test_happy_path_reserves_one_slot(self):
        g = _BudgetGate(window_seconds=300, max_calls=10, min_interval=0)
        with mock.patch.object(ta, "_http_get_json", return_value={"tweets": []}):
            out = g.fetch(("advanced_search", "q", "Top", ""),
                          "https://api.twitterapi.io/twitter/tweet/advanced_search?q=1", "k", 5)
        self.assertEqual(out, {"tweets": []})
        self.assertEqual(len(g._window), 1)

    def test_window_cap_denies_without_socket(self):
        g = _BudgetGate(window_seconds=300, max_calls=1, min_interval=0)
        m = mock.Mock(return_value={"tweets": []})
        with mock.patch.object(ta, "_http_get_json", m):
            g.fetch(("a",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
            # second DISTINCT call: window already full -> denied, no 2nd socket
            out2 = g.fetch(("b",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=2", "k", 5)
        self.assertIsNone(out2)
        self.assertEqual(m.call_count, 1)

    def test_window_evicts_expired_then_allows(self):
        g = _BudgetGate(window_seconds=300, max_calls=1, min_interval=0)
        # Pre-load an expired timestamp so eviction frees the single slot.
        with mock.patch.object(ta.time, "monotonic", return_value=1000.0):
            g._window.append(0.0)  # far in the past
            m = mock.Mock(return_value={"tweets": []})
            with mock.patch.object(ta, "_http_get_json", m):
                out = g.fetch(("a",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
        self.assertEqual(out, {"tweets": []})
        self.assertEqual(m.call_count, 1)

    def test_retry_reserves_a_second_slot(self):
        # 429 then 200: BOTH attempts reserve a window slot (count every sent req).
        g = _BudgetGate(window_seconds=300, max_calls=10, min_interval=0)
        err = urllib.error.HTTPError("u", 429, "Too Many", None, None)
        m = mock.Mock(side_effect=[err, {"tweets": [{"text": "ok"}]}])
        with mock.patch.object(ta, "_http_get_json", m):
            out = g.fetch(("a",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(len(g._window), 2)  # retry counted as a second billable call
        self.assertEqual(out["tweets"][0]["text"], "ok")

    def test_retry_blocked_when_window_fills(self):
        # max_calls=1: first attempt 429s and reserves the only slot; the retry's
        # reservation is denied, so no second socket opens.
        g = _BudgetGate(window_seconds=300, max_calls=1, min_interval=0)
        err = urllib.error.HTTPError("u", 429, "Too Many", None, None)
        m = mock.Mock(side_effect=[err, {"tweets": [{"text": "never"}]}])
        with mock.patch.object(ta, "_http_get_json", m):
            out = g.fetch(("a",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
        self.assertIsNone(out)
        self.assertEqual(m.call_count, 1)  # retry never reached the socket

    def test_cache_stores_success_only(self):
        g = _BudgetGate(window_seconds=300, max_calls=10, min_interval=0)
        # success is cached -> 2nd identical fetch is a free cache hit
        m = mock.Mock(return_value={"tweets": []})
        with mock.patch.object(ta, "_http_get_json", m):
            g.fetch(("k1",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
            g.fetch(("k1",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
        self.assertEqual(m.call_count, 1)  # cache hit, no 2nd call, no 2nd reserve
        self.assertEqual(len(g._window), 1)
        # a hard error is NOT cached -> an identical later fetch re-attempts; the
        # key is never permanently poisoned for the life of the process.
        err = urllib.error.HTTPError("u", 403, "Forbidden", None, None)
        m2 = mock.Mock(side_effect=err)
        with mock.patch.object(ta, "_http_get_json", m2):
            self.assertIsNone(g.fetch(("k2",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=2", "k", 5))
            self.assertIsNone(g.fetch(("k2",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=2", "k", 5))
        self.assertEqual(m2.call_count, 2)  # re-attempted, not served a stale None
        self.assertNotIn(("k2",), g._cache)

    def test_budget_denial_not_cached_then_succeeds_after_evict(self):
        # MEDIUM fix: a budget denial must NOT be cached, so once the rolling
        # window evicts, the SAME key re-checks the budget and can succeed.
        g = _BudgetGate(window_seconds=300, max_calls=1, min_interval=0)
        ok = {"tweets": [{"text": "ok"}]}
        m = mock.Mock(return_value=ok)
        target = "https://api.twitterapi.io/twitter/tweet/advanced_search?x=9"
        with mock.patch.object(ta, "_http_get_json", m):
            with mock.patch.object(ta.time, "monotonic", return_value=1000.0):
                # fill the single window slot with a DIFFERENT key
                g.fetch(("other",), "https://api.twitterapi.io/twitter/tweet/advanced_search?x=1", "k", 5)
                # target key is denied (window full) -> None, and must NOT be cached
                self.assertIsNone(g.fetch(("target",), target, "k", 5))
            self.assertNotIn(("target",), g._cache)
            # advance time so the window evicts, then the SAME key now succeeds
            with mock.patch.object(ta.time, "monotonic", return_value=2000.0):
                out = g.fetch(("target",), target, "k", 5)
        self.assertEqual(out, ok)


class TestFormat(unittest.TestCase):
    def test_empty_text_dropped(self):
        self.assertIsNone(format_tweet({"text": "   ", "id": "1"}))
        self.assertIsNone(format_tweet({"id": "1"}))

    def test_full_tweet_has_raw_content_over_100(self):
        item = format_tweet(_tweet())
        self.assertIsNotNone(item)
        self.assertGreater(len(item["raw_content"]), 100)
        self.assertIn("@alice", item["raw_content"])
        self.assertIn("verified", item["raw_content"])
        self.assertIn("likes", item["raw_content"])
        self.assertEqual(item["_tweet_id"], "1")

    def test_thin_tweet_under_100_dropped(self):
        # short text WITH a valid href -> formatted block <= 100 -> dropped by >100-or-drop
        self.assertIsNone(format_tweet({"text": "hi", "id": "1", "author": {"userName": "z"}}))

    def test_empty_href_dropped(self):
        # long text but no url and no id/handle to synthesize one -> dropped as malformed
        self.assertIsNone(format_tweet({"text": "x" * 200, "author": {}}))
        # a real id + handle synthesizes a valid href -> kept
        item = format_tweet({"text": "x" * 200, "id": "55", "author": {"userName": "carol"}})
        self.assertIsNotNone(item)
        self.assertEqual(item["href"], "https://x.com/carol/status/55")

    def test_quoted_tweet_text_included(self):
        tw = _tweet(quoted_tweet={"text": "the original claim being quoted here",
                                  "author": {"userName": "bob"}})
        item = format_tweet(tw)
        self.assertIn("Quoting @bob", item["raw_content"])
        self.assertIn("original claim", item["raw_content"])

    def test_min_text_chars_drop(self):
        # a tweet with real-but-short text gets dropped when min_text_chars is set
        tw = _tweet(text="short one")
        self.assertIsNone(format_tweet(tw, min_text_chars=50))
        self.assertIsNotNone(format_tweet(tw, min_text_chars=0))

    def test_char_cap(self):
        tw = _tweet(text="z" * 5000)
        item = format_tweet(tw, max_chars=300)
        self.assertLessEqual(len(item["raw_content"]), 302)

    def test_bool_engagement_not_rendered_as_number(self):
        # isBlueVerified is a bool; ensure bool counts never leak into engagement
        tw = _tweet(likeCount=True, retweetCount=5)
        item = format_tweet(tw)
        self.assertIn("5 RTs", item["raw_content"])
        self.assertNotIn("1 likes", item["raw_content"])  # True must not become "1 likes"


class TestReplies(unittest.TestCase):
    def test_append_replies_dedups_and_bounds(self):
        item = format_tweet(_tweet())
        base_len = len(item["raw_content"])
        replies = [
            {"text": "first reply", "author": {"userName": "r1"}, "id": "a"},
            {"text": "first reply", "author": {"userName": "r1"}, "id": "a"},  # dup id
            {"text": "second reply here", "author": {"userName": "r2"}, "id": "b"},
            {"text": "", "author": {"userName": "r3"}},  # empty -> skipped
            {"text": "third reply", "author": {"userName": "r4"}, "id": "d"},
        ]
        ta._append_replies(item, replies, max_replies=2, max_chars=5000)
        self.assertIn("Top replies (2)", item["raw_content"])
        self.assertIn("@r1", item["raw_content"])
        self.assertIn("@r2", item["raw_content"])
        self.assertNotIn("@r4", item["raw_content"])  # bounded to 2
        self.assertEqual(item["raw_content"].count("first reply"), 1)  # dedup
        self.assertGreater(len(item["raw_content"]), base_len)


class TestSearch(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ta.time, "sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)
        # default-on key for most search tests
        e = mock.patch.dict(os.environ, {"TWITTERAPI_IO_KEY": "test-key"})
        e.start()
        self.addCleanup(e.stop)

    def _with_fresh_gate(self, max_calls=100):
        g = _BudgetGate(window_seconds=300, max_calls=max_calls, min_interval=0)
        return mock.patch.object(ta, "get_default_gate", return_value=g), g

    def test_missing_key_returns_empty(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TWITTERAPI_IO_KEY", None)
            r = TwitterAPISearch("anything")
            self.assertEqual(r.search(max_results=5), [])

    def test_results_carry_raw_content_and_strip_internal_keys(self):
        payload = {"tweets": [_tweet(tid="1"), _tweet(tid="2", handle="bob")],
                   "has_next_page": False}
        gate_patch, _ = self._with_fresh_gate()
        with gate_patch, mock.patch.object(ta, "_http_get_json", return_value=payload):
            out = TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(len(out), 2)
        for item in out:
            self.assertGreater(len(item["raw_content"]), 100)
            self.assertNotIn("_tweet_id", item)   # internal keys stripped
            self.assertNotIn("_raw_lines", item)
            self.assertTrue(item["href"].startswith("https://x.com/"))

    def test_never_returns_bare_href_when_all_thin(self):
        # tweets too thin to format -> dropped -> NOT returned as bare x.com hrefs
        payload = {"tweets": [{"text": "hi", "id": "1", "author": {}}],
                   "has_next_page": False}
        gate_patch, _ = self._with_fresh_gate()
        with gate_patch, mock.patch.object(ta, "_http_get_json", return_value=payload):
            out = TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(out, [])

    def test_pagination_bounded_by_max_pages(self):
        # API always says has_next_page; the retriever must still stop at max_pages
        payload = {"tweets": [_tweet(tid="1")], "has_next_page": True, "next_cursor": "c"}
        gate_patch, _ = self._with_fresh_gate()
        m = mock.Mock(return_value=payload)
        with gate_patch, mock.patch.object(ta, "_http_get_json", m), \
             mock.patch.object(ta, "TWITTERAPI_MAX_PAGES_PER_QUERY", 2), \
             mock.patch.object(ta, "TWITTERAPI_MAX_SEARCH_CALLS", 2):
            out = TwitterAPISearch("q").search(max_results=100)
        self.assertEqual(m.call_count, 2)  # never an unbounded cursor loop
        self.assertGreaterEqual(len(out), 1)

    def test_budget_exhaustion_degrades_to_empty(self):
        gate_patch, _ = self._with_fresh_gate(max_calls=0)  # window full from the start
        m = mock.Mock(return_value={"tweets": [_tweet()]})
        with gate_patch, mock.patch.object(ta, "_http_get_json", m):
            out = TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(out, [])
        m.assert_not_called()  # no paid call escaped the budget

    def test_http_error_degrades_to_empty(self):
        gate_patch, _ = self._with_fresh_gate()
        err = urllib.error.HTTPError("u", 500, "err", None, None)
        with gate_patch, mock.patch.object(ta, "_http_get_json", mock.Mock(side_effect=err)):
            out = TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(out, [])  # never raises into the conductor

    def test_replies_opt_in_off_by_default(self):
        payload = {"tweets": [_tweet(tid="1")], "has_next_page": False}
        gate_patch, _ = self._with_fresh_gate()
        m = mock.Mock(return_value=payload)
        with gate_patch, mock.patch.object(ta, "_http_get_json", m):
            # default TWITTERAPI_REPLY_TOP_K == 0 -> exactly one (search) call
            TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(m.call_count, 1)

    def test_replies_enrich_when_enabled(self):
        search_payload = {"tweets": [_tweet(tid="42")], "has_next_page": False}
        reply_payload = {"tweets": [{"text": "a real reply", "author": {"userName": "r"}, "id": "x"}]}
        gate_patch, _ = self._with_fresh_gate()

        def _route(url, *a, **k):
            return reply_payload if "replies" in url else search_payload

        with gate_patch, mock.patch.object(ta, "_http_get_json", side_effect=_route), \
             mock.patch.object(ta, "TWITTERAPI_REPLY_TOP_K", 1), \
             mock.patch.object(ta, "TWITTERAPI_MAX_REPLY_CALLS", 1):
            out = TwitterAPISearch("q").search(max_results=10)
        self.assertEqual(len(out), 1)
        self.assertIn("Top replies", out[0]["raw_content"])
        self.assertIn("a real reply", out[0]["raw_content"])

    def test_constructor_accepts_extra_kwargs(self):
        # robust across both instantiation styles (xquik passes **kwargs)
        r = TwitterAPISearch("q", query_domains=["x.com"], extra="ignored", websocket=None)
        self.assertEqual(r.query, "q")


if __name__ == "__main__":
    unittest.main()
