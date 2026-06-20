"""Bug-first tests for the Arctic Shift Reddit-comment resolver.

Run from the gpt-researcher repo root with the project venv:
    .venv/bin/python -m pytest gpt_researcher/retrievers/social_openai/test_reddit_resolver.py
or, dependency-free:
    .venv/bin/python -m unittest gpt_researcher.retrievers.social_openai.test_reddit_resolver

These assert CORRECT behavior (the safety contract), so a regression that
re-opens the "never scrape reddit.com" hole fails the suite. NO test ever
contacts a live network — Arctic Shift HTTP is mocked.
"""

import unittest
import urllib.error
from unittest import mock

from . import reddit_resolver as rr
from .reddit_resolver import (
    ArcticShiftBackend,
    RedditCommentResolver,
    format_comments,
    is_reddit_url,
    parse_post_id,
    _host_allowed,
    _http_get_json,
)


class TestParsing(unittest.TestCase):
    def test_thread_url_variants_yield_id(self):
        cases = {
            "https://www.reddit.com/r/python/comments/1txuzkb/some_slug/": "1txuzkb",
            "https://old.reddit.com/r/Python/comments/1txuzkb/some_slug": "1txuzkb",
            "https://reddit.com/comments/1txuzkb": "1txuzkb",
            "https://np.reddit.com/r/x/comments/abc123/t/": "abc123",
            # comment permalink — thread id is still the /comments/<id> segment
            "https://www.reddit.com/r/x/comments/1txuzkb/slug/labc999/": "1txuzkb",
            "https://www.reddit.com/r/x/comments/1txuzkb/slug/?utm=1#c": "1txuzkb",
            "https://redd.it/1txuzkb": "1txuzkb",
        }
        for url, expected in cases.items():
            self.assertEqual(parse_post_id(url), expected, url)

    def test_non_thread_reddit_urls_yield_none(self):
        for url in [
            "https://www.reddit.com/r/python/",
            "https://www.reddit.com/user/spez",
            "https://www.reddit.com/search/?q=x",
            "https://reddit.com/",
        ]:
            self.assertIsNone(parse_post_id(url), url)

    def test_non_reddit_hosts_never_parse(self):
        # Look-alike host must NOT be treated as reddit.
        self.assertFalse(is_reddit_url("https://notreddit.com/comments/abc"))
        self.assertIsNone(parse_post_id("https://notreddit.com/comments/abc"))
        self.assertIsNone(parse_post_id("https://example.com/r/x/comments/abc"))

    def test_is_reddit_url(self):
        for u in ["https://reddit.com/x", "https://www.reddit.com/x",
                  "https://old.reddit.com/x", "https://redd.it/abc"]:
            self.assertTrue(is_reddit_url(u), u)
        for u in ["https://notreddit.com/x", "https://example.com",
                  "https://x.com/i", "https://youtube.com/watch"]:
            self.assertFalse(is_reddit_url(u), u)


class TestHostAllowlist(unittest.TestCase):
    def test_allowlist_accepts_arctic_shift_only(self):
        self.assertTrue(_host_allowed("arctic-shift.photon-reddit.com"))
        self.assertTrue(_host_allowed("photon-reddit.com"))
        self.assertTrue(_host_allowed("api.photon-reddit.com"))

    def test_allowlist_rejects_reddit_and_lookalikes(self):
        for h in ["reddit.com", "www.reddit.com", "arctic-shift.reddit.com",
                  "evilphoton-reddit.com", "photon-reddit.com.evil.com",
                  "example.com"]:
            self.assertFalse(_host_allowed(h), h)

    def test_http_get_json_refuses_non_https(self):
        with self.assertRaises(ValueError):
            _http_get_json("http://arctic-shift.photon-reddit.com/api/x", 5)

    def test_http_get_json_refuses_reddit_host(self):
        with self.assertRaises(ValueError):
            _http_get_json("https://www.reddit.com/api/x", 5)

    def test_backend_refuses_reddit_base_url(self):
        # Configuring the backend to point at reddit.com must fail loudly.
        with self.assertRaises(ValueError):
            ArcticShiftBackend(base_url="https://www.reddit.com/api")

    def test_redirect_to_reddit_is_refused(self):
        # A 3xx pointing at reddit.com must raise rather than be followed.
        import urllib.request
        h = rr._RefuseRedirect()
        req = urllib.request.Request("https://arctic-shift.photon-reddit.com/api/x")
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(req, None, 302, "Found", {}, "https://www.reddit.com/evil")


class TestFormat(unittest.TestCase):
    def test_filters_deleted_and_empty(self):
        comments = [
            {"body": "[deleted]", "author": "a", "score": 5},
            {"body": "", "author": "b", "score": 5},
            {"body": "[removed]", "author": "c", "score": 5},
            {"body": "real content here that is meaningful", "author": "d", "score": 1},
        ]
        out = format_comments(comments, title="T", body="B")
        self.assertIn("real content here", out)
        self.assertNotIn("[deleted]", out)
        self.assertNotIn("[removed]", out)
        self.assertIn("u/d", out)

    def test_prepends_title_and_body(self):
        out = format_comments([{"body": "x" * 50, "author": "z"}], title="MyTitle", body="OPBody")
        self.assertIn("MyTitle", out)
        self.assertIn("OPBody", out)

    def test_preserves_api_order_not_score(self):
        # Design: Arctic Shift API order is PRIMARY; score must NOT reorder.
        comments = [
            {"body": "low score but first", "author": "a", "score": 1},
            {"body": "high score but second", "author": "b", "score": 99},
        ]
        out = format_comments(comments, max_comments=10)
        self.assertLess(out.index("u/a"), out.index("u/b"))  # API order kept

    def test_dedup_repeated_comments(self):
        # Same comment id returned twice -> rendered once (the real archive case).
        by_id = [
            {"body": "alpha text", "author": "a", "id": "c1"},
            {"body": "alpha text", "author": "a", "id": "c1"},
        ]
        self.assertEqual(format_comments(by_id, max_comments=10).count("alpha text"), 1)
        # No ids: dedup by normalized body text instead.
        by_body = [
            {"body": "beta text", "author": "a"},
            {"body": "beta text", "author": "b"},
        ]
        self.assertEqual(format_comments(by_body, max_comments=10).count("beta text"), 1)

    def test_char_cap(self):
        big = [{"body": "y" * 500, "author": f"u{i}", "score": 0} for i in range(50)]
        out = format_comments(big, max_chars=300)
        self.assertLessEqual(len(out), 302)


class TestBackend(unittest.TestCase):
    def setUp(self):
        # no real sleeping (throttle + retry)
        p = mock.patch.object(rr.time, "sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)

    def _backend(self):
        return ArcticShiftBackend(min_interval=0.0)

    def test_happy_path_returns_comment_list(self):
        payload = {"data": [{"body": "hello", "author": "a", "score": 3}]}
        with mock.patch.object(rr, "_http_get_json", return_value=payload):
            out = self._backend().fetch_comments("abc123")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["body"], "hello")

    def test_non_list_data_returns_empty(self):
        with mock.patch.object(rr, "_http_get_json", return_value={"data": {"not": "list"}}):
            self.assertEqual(self._backend().fetch_comments("abc123"), [])

    def test_http_403_returns_empty_and_negative_caches(self):
        err = urllib.error.HTTPError("https://x", 403, "Forbidden", None, None)
        m = mock.Mock(side_effect=err)
        with mock.patch.object(rr, "_http_get_json", m):
            b = self._backend()
            self.assertEqual(b.fetch_comments("abc123"), [])
            self.assertEqual(b.fetch_comments("abc123"), [])  # cached, no 2nd call
        self.assertEqual(m.call_count, 1)

    def test_429_then_200_retries(self):
        err = urllib.error.HTTPError("https://x", 429, "Too Many", None, None)
        ok = {"data": [{"body": "ok", "author": "a"}]}
        m = mock.Mock(side_effect=[err, ok])
        with mock.patch.object(rr, "_http_get_json", m):
            out = self._backend().fetch_comments("abc123")
        self.assertEqual(m.call_count, 2)
        self.assertEqual(out[0]["body"], "ok")

    def test_cache_dedups_repeat_calls(self):
        payload = {"data": [{"body": "x" * 40, "author": "a"}]}
        m = mock.Mock(return_value=payload)
        with mock.patch.object(rr, "_http_get_json", m):
            b = self._backend()
            b.fetch_comments("abc123")
            b.fetch_comments("abc123")
        self.assertEqual(m.call_count, 1)

    def test_bad_post_id_no_request(self):
        m = mock.Mock()
        with mock.patch.object(rr, "_http_get_json", m):
            self.assertEqual(self._backend().fetch_comments("../etc"), [])
        m.assert_not_called()


class _FakeBackend:
    name = "fake"

    def __init__(self, comments):
        self._comments = comments

    def fetch_comments(self, post_id):
        return list(self._comments)


class TestResolver(unittest.TestCase):
    def test_thread_resolves_to_comment_text(self):
        comments = [{"body": "a real substantive comment " * 5, "author": "u", "score": 2}]
        r = RedditCommentResolver(backends=[_FakeBackend(comments)])
        out = r.resolve("https://www.reddit.com/r/x/comments/abc123/s/", title="T", body="B")
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 100)
        self.assertIn("a real substantive comment", out)

    def test_thread_no_comments_falls_back_to_snippet(self):
        r = RedditCommentResolver(backends=[_FakeBackend([])])
        long_body = "This is the OpenAI snippet body with enough characters to exceed one hundred chars threshold easily."
        out = r.resolve("https://www.reddit.com/r/x/comments/abc123/s/", title="Q", body=long_body)
        self.assertIsNotNone(out)
        self.assertIn(long_body, out)
        self.assertIn("snippet", out.lower())

    def test_short_snippet_drops_to_none(self):
        # >100-or-drop: a thread with no comments and a tiny snippet -> None
        r = RedditCommentResolver(backends=[_FakeBackend([])])
        out = r.resolve("https://www.reddit.com/r/x/comments/abc123/s/", title="hi", body="short")
        self.assertIsNone(out)

    def test_non_thread_reddit_url_snippet_or_none(self):
        r = RedditCommentResolver(backends=[_FakeBackend([])])
        long_body = "z" * 120
        out = r.resolve("https://www.reddit.com/r/python/", title="Subreddit", body=long_body)
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 100)

    def test_non_reddit_url_returns_none(self):
        r = RedditCommentResolver(backends=[_FakeBackend([{"body": "x" * 200, "author": "a"}])])
        self.assertIsNone(r.resolve("https://example.com/page", title="T", body="B"))

    def test_disabled_backend_still_snippet_or_drop(self):
        # ARCTIC_SHIFT_ENABLED=false path == no backends: never returns a bare href,
        # only snippet (>100) or None. Crucially does NOT raise / does NOT scrape.
        r = RedditCommentResolver(backends=[])
        long_body = "w" * 130
        out = r.resolve("https://www.reddit.com/r/x/comments/abc123/s/", title="", body=long_body)
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 100)

    def test_env_disabled_builds_no_backends(self):
        # Env-driven disable: ARCTIC_SHIFT_ENABLED false -> resolver has no backends
        # (so it never calls the API), but still does snippet-or-drop.
        with mock.patch.object(rr, "ARCTIC_SHIFT_ENABLED", False):
            r = RedditCommentResolver()
        self.assertEqual(r.backends, [])

    def test_all_deleted_comments_fall_back_to_op_context(self):
        # A thread whose comments are all [deleted]/[removed] must still carry the
        # OP title/body (>100), never a bare scrapeable href.
        r = RedditCommentResolver(backends=[_FakeBackend([{"body": "[deleted]", "author": "a"}])])
        long_body = ("useful OP context that comfortably exceeds one hundred characters "
                     "so the fallback carries real content here")
        out = r.resolve("https://www.reddit.com/r/x/comments/abc123/s/", title="Q", body=long_body)
        self.assertIsNotNone(out)
        self.assertIn(long_body, out)


class TestEnrichAndSelect(unittest.TestCase):
    """The retriever-level seam: reddit -> raw_content or drop; never bare href."""

    def setUp(self):
        import os
        p = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
        p.start()
        self.addCleanup(p.stop)
        from .social_openai import SocialOpenAIRetriever
        self.R = SocialOpenAIRetriever

    def _patch_resolver(self, resolver):
        # _enrich_and_select calls get_default_resolver imported into social_openai
        return mock.patch(
            "gpt_researcher.retrievers.social_openai.social_openai.get_default_resolver",
            return_value=resolver,
        )

    def test_reddit_gets_raw_content_non_reddit_passthrough(self):
        comments = [{"body": "useful answer " * 20, "author": "u", "score": 5}]
        resolver = RedditCommentResolver(backends=[_FakeBackend(comments)])
        retr = self.R("q", query_domains=["reddit.com"])
        candidates = [
            {"href": "https://www.reddit.com/r/x/comments/abc123/s/", "title": "T", "body": "B"},
            {"href": "https://x.com/some/post", "title": "X", "body": "snippet"},
        ]
        with self._patch_resolver(resolver):
            out = retr._enrich_and_select(candidates, max_results=10)
        self.assertEqual(len(out), 2)
        reddit_item = [o for o in out if "reddit.com" in o["href"]][0]
        self.assertIn("raw_content", reddit_item)
        self.assertGreater(len(reddit_item["raw_content"]), 100)
        x_item = [o for o in out if "x.com" in o["href"]][0]
        self.assertNotIn("raw_content", x_item)  # untouched

    def test_unresolvable_reddit_is_dropped_never_bare(self):
        resolver = RedditCommentResolver(backends=[_FakeBackend([])])  # no comments
        retr = self.R("q", query_domains=["reddit.com"])
        candidates = [
            {"href": "https://www.reddit.com/r/x/comments/abc123/s/", "title": "hi", "body": "x"},  # tiny -> drop
            {"href": "https://youtube.com/watch?v=1", "title": "Y", "body": "B"},
        ]
        with self._patch_resolver(resolver):
            out = retr._enrich_and_select(candidates, max_results=10)
        # The reddit item MUST be gone (no bare reddit href to the scraper)
        self.assertTrue(all("reddit.com" not in o.get("href", "") for o in out))
        self.assertEqual(len(out), 1)

    def test_backfill_keeps_count_when_reddit_dropped(self):
        resolver = RedditCommentResolver(backends=[_FakeBackend([])])  # all reddit unresolvable
        retr = self.R("q", query_domains=["reddit.com"])
        # First two are unresolvable reddit; later non-reddit should backfill.
        candidates = [
            {"href": "https://www.reddit.com/r/x/comments/aaa111/s/", "title": "h", "body": "x"},
            {"href": "https://www.reddit.com/r/x/comments/bbb222/s/", "title": "h", "body": "x"},
            {"href": "https://x.com/a", "title": "1", "body": "b"},
            {"href": "https://youtube.com/b", "title": "2", "body": "b"},
        ]
        with self._patch_resolver(resolver):
            out = retr._enrich_and_select(candidates, max_results=2)
        self.assertEqual(len(out), 2)
        self.assertTrue(all("reddit.com" not in o["href"] for o in out))

    def test_resolver_unavailable_drops_reddit_keeps_rest(self):
        # If resolver setup itself raises, reddit URLs must still be dropped (never
        # scraped) and non-reddit results kept — the safety invariant must not
        # depend on the resolver being constructible.
        retr = self.R("q", query_domains=["reddit.com"])
        candidates = [
            {"href": "https://www.reddit.com/r/x/comments/abc/s/", "title": "T", "body": "B"},
            {"href": "https://x.com/a", "title": "X", "body": "b"},
        ]
        with mock.patch(
            "gpt_researcher.retrievers.social_openai.social_openai.get_default_resolver",
            side_effect=RuntimeError("boom"),
        ):
            out = retr._enrich_and_select(candidates, max_results=10)
        self.assertTrue(all("reddit.com" not in o.get("href", "") for o in out))
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
