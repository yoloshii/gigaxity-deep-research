#!/usr/bin/env python3
"""Jina MCP — self-hosted Triple Stack search/read layer.

Replaces the hosted `https://mcp.jina.ai/v1` server, whose search family routes
through `svip.jina.ai` — a paid-only lane that refuses trial credits and reports
the refusal as an opaque HTTP 500 "Internal Server Error".

Design rules earned the hard way (see README "Why this exists"):

1. NEVER send `num` to s.jina.ai. Its validator rejects `num > 20` with a 400,
   and the upstream server declares a default of 30. We request the default page
   and slice client-side, so the cap can never be tripped.
2. ALWAYS surface the response BODY on failure, never `statusText` alone. The
   hosted server discarding the body is exactly what turned "Not enough credits"
   into a multi-day misdiagnosis.
3. Search endpoint is CONFIGURABLE (`standard` | `vip`), never hardcoded.
4. Academic search does not touch Jina at all — arXiv and SSRN have free,
   precise, key-less APIs that outperform the `domain:` param they replace
   (which `s.jina.ai` silently ignores).

Tool names match the hosted server exactly, so agent routing rules that call
`mcp__jina__search_web` keep working unchanged.
"""

import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

_original_print = print


def print(*args, **kwargs):  # noqa: A001 - stdout is the MCP transport
    kwargs.setdefault("file", sys.stderr)
    _original_print(*args, **kwargs)


from mcp.server.fastmcp import FastMCP  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────────────

JINA_API_KEY = os.environ.get("JINA_API_KEY", "").strip()
SEARCH_ENDPOINT = os.environ.get("JINA_SEARCH_ENDPOINT", "standard").strip().lower()
GHOST_KEY = os.environ.get("JINA_GHOST_KEY", "").strip()
TIMEOUT = int(os.environ.get("JINA_TIMEOUT", "60"))
MAX_PARALLEL = int(os.environ.get("JINA_MAX_PARALLEL", "5"))
USER_AGENT = "gigaxity-jina-mcp/1.0"

READER_URL = "https://r.jina.ai/"
SEARCH_STANDARD = "https://s.jina.ai/"
SEARCH_VIP = "https://svip.jina.ai/"
API_BASE = "https://api.jina.ai"
ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_SSRN_SOURCE = "S4210172589"  # SSRN Electronic Journal
DBLP_API = "https://dblp.org/search/publ/api"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# s.jina.ai rejects num > 20 (validator: v >= 0 && v <= 20).
SEARCH_NUM_CAP = 20

EMBED_MODEL = os.environ.get("JINA_EMBED_MODEL", "jina-embeddings-v5-text-small")
RERANK_MODEL = os.environ.get("JINA_RERANK_MODEL", "jina-reranker-v3")

if not JINA_API_KEY:
    print("ERROR: JINA_API_KEY must be set in the environment.")
    sys.exit(1)

if SEARCH_ENDPOINT not in ("standard", "vip"):
    print(f"ERROR: JINA_SEARCH_ENDPOINT must be 'standard' or 'vip' (got {SEARCH_ENDPOINT!r}).")
    sys.exit(1)

mcp = FastMCP(
    "jina",
    instructions=(
        "Web access and online content retrieval. Search the live web, read URLs as clean "
        "markdown, search arXiv and SSRN, rerank and deduplicate result sets, classify text, "
        "and extract PDF layout. Self-hosted: web search uses s.jina.ai, academic search uses "
        "the arXiv and OpenAlex APIs directly. NOT for local files, code execution, or "
        "database queries."
    ),
)


# ── HTTP plumbing ────────────────────────────────────────────────────────────


def _diagnose(status: int, body: str) -> str:
    """Turn a failure into an ACTIONABLE message.

    The hosted server's cardinal sin was returning `response.statusText` and
    dropping the body — a billing condition then reads as a server outage. Every
    error path here reports what the server actually said, plus what to do.
    """
    low = body.lower()

    if "not enough credits" in low:
        return (
            f"HTTP {status}: svip.jina.ai (the VIP search lane) refused this key with "
            f"'Not enough credits'. That lane requires a PAID regular_balance — trial "
            f"credits are refused outright, so rotating to a fresh trial key will NOT "
            f"help. Set JINA_SEARCH_ENDPOINT=standard (the default) to use s.jina.ai. "
            f"Raw body: {body[:300]}"
        )
    if "run out of its token" in low or "insufficient balance" in low or status == 402:
        return (
            f"HTTP {status}: this API key is genuinely out of tokens. Top up or rotate "
            f"the key at https://jina.ai. Raw body: {body[:300]}"
        )
    if status == 401 or "unauthorized" in low or "invalid api key" in low:
        return (
            f"HTTP {status}: key rejected. Check JINA_API_KEY. Note that a 401 on search "
            f"while URL reads still succeed usually means an upstream auth bug, not a bad "
            f"key. Raw body: {body[:300]}"
        )
    if status == 429 or "rate limit" in low:
        return f"HTTP {status}: rate limited — retry after a short backoff. Raw body: {body[:300]}"
    if "fetch failed" in low and "svip" in low:
        return (
            f"HTTP {status}: s.jina.ai could not reach its internal search upstream "
            f"(svip.jina.ai). This affects SITE-RESTRICTED searches specifically; "
            f"unrestricted queries still work. Drop the site filter and retry. "
            f"Raw body: {body[:300]}"
        )
    return f"HTTP {status}: {body[:400]}"


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
    auth: bool = True,
    timeout: int | None = None,
) -> tuple[bool, str]:
    """Perform an HTTP call. Returns (ok, text). On failure text is diagnosed.

    A User-Agent is always sent: several upstreams (Cloudflare-fronted Jina hosts,
    export.arxiv.org) return 403 or an empty body to UA-less clients.
    """
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    if auth and JINA_API_KEY:
        hdrs["Authorization"] = f"Bearer {JINA_API_KEY}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return False, _diagnose(exc.code, raw)
    except urllib.error.URLError as exc:
        return False, f"Network error contacting {urllib.parse.urlparse(url).netloc}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - surface anything to the agent
        return False, f"{type(exc).__name__}: {exc}"


def _json_request(url: str, **kw) -> tuple[bool, dict | str]:
    ok, text = _request(url, **kw)
    if not ok:
        return False, text
    try:
        return True, json.loads(text)
    except json.JSONDecodeError:
        return False, f"Upstream returned non-JSON: {text[:300]}"


def _fan_out(fn, items: list) -> list:
    """Run fn over items concurrently, preserving input order."""
    if not items:
        return []
    workers = min(MAX_PARALLEL, len(items))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


# ── Search backends ──────────────────────────────────────────────────────────


def _search_web(query: str, num: int, site: str | None, tbs: str | None,
                gl: str | None, hl: str | None) -> str:
    """One web search.

    RULE 1 in practice: `num` is never sent upstream. s.jina.ai caps it at 20 and
    400s above that; we take the server's default page and slice locally, so no
    caller-supplied count can ever produce a validator rejection.
    """
    params = {"q": query}
    for key, val in (("tbs", tbs), ("gl", gl), ("hl", hl)):
        if val:
            params[key] = val

    base = SEARCH_VIP if SEARCH_ENDPOINT == "vip" else SEARCH_STANDARD
    url = f"{base}?{urllib.parse.urlencode(params)}"

    headers = {"X-Respond-With": "no-content"}
    if site:
        headers["X-Site"] = site

    ok, payload = _json_request(url, headers=headers)
    if not ok:
        return f"Search failed for {query!r}: {payload}"

    # standard returns {"data": [...]}, vip returns {"results": [...]}
    rows = payload.get("data") or payload.get("results") or []
    rows = rows[: max(1, min(num, SEARCH_NUM_CAP))]
    if not rows:
        return f"No results for {query!r}."

    out = [f"Results for {query!r}:"]
    for i, r in enumerate(rows, 1):
        out.append(f"[{i}] {r.get('title', 'Untitled')}")
        out.append(f"[{i}] URL: {r.get('url', '')}")
        desc = r.get("description") or r.get("snippet")
        if desc:
            out.append(f"[{i}] {desc}")
        if r.get("date"):
            out.append(f"[{i}] Date: {r['date']}")
        out.append("")
    return "\n".join(out)


def _search_arxiv(query: str, num: int, sort: str) -> str:
    """arXiv search via the official export API.

    Deliberately NOT routed through Jina. The hosted server scoped arXiv with a
    `domain: 'arxiv'` body param that only svip understands — s.jina.ai accepts
    the param and silently ignores it, returning general web results. The native
    API is free, needs no key, and supports field queries (`cat:`, `abs:`, `au:`)
    and date sorting that the Jina wrapper never exposed.
    """
    sort_by = {"relevance": "relevance",
               "date": "submittedDate",
               "updated": "lastUpdatedDate"}.get(sort, "relevance")
    params = {
        "search_query": query if ":" in query else f"all:{query}",
        "start": 0,
        "max_results": max(1, min(num, 50)),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    ok, text = _request(f"{ARXIV_API}?{urllib.parse.urlencode(params)}", auth=False)
    if not ok:
        return f"arXiv search failed for {query!r}: {text}"

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return f"arXiv returned unparseable XML: {exc}"

    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    if not entries:
        return f"No arXiv papers for {query!r}."

    out = [f"arXiv results for {query!r}:"]
    for i, e in enumerate(entries, 1):
        title = (e.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
        summary = (e.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
        published = (e.findtext("a:published", "", ns) or "")[:10]
        link = (e.findtext("a:id", "", ns) or "").strip()
        authors = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
        cats = [c.get("term") for c in e.findall("a:category", ns) if c.get("term")]
        out.append(f"[{i}] {title}")
        out.append(f"[{i}] URL: {link}")
        if authors:
            shown = ", ".join(a for a in authors[:5] if a)
            out.append(f"[{i}] Authors: {shown}{' et al.' if len(authors) > 5 else ''}")
        out.append(f"[{i}] Published: {published}" + (f" | {', '.join(cats[:4])}" if cats else ""))
        if summary:
            out.append(f"[{i}] {summary[:400]}{'...' if len(summary) > 400 else ''}")
        out.append("")
    return "\n".join(out)


def _search_ssrn(query: str, num: int) -> str:
    """SSRN search via OpenAlex, filtered to the SSRN Electronic Journal source.

    SSRN has no public search API and the hosted server's `domain: 'ssrn'` param
    is svip-only. OpenAlex indexes SSRN with resolvable 10.2139/ssrn.* DOIs, is
    free and key-less, and lets us filter to the SSRN source exactly.
    """
    params = {
        "search": query,
        "filter": f"primary_location.source.id:{OPENALEX_SSRN_SOURCE}",
        "per-page": max(1, min(num, 50)),
        "mailto": "research@gigaxity.local",  # OpenAlex polite pool
    }
    ok, payload = _json_request(f"{OPENALEX_API}?{urllib.parse.urlencode(params)}", auth=False)
    if not ok:
        return f"SSRN search failed for {query!r}: {payload}"

    rows = payload.get("results", [])
    if not rows:
        return f"No SSRN papers for {query!r}."

    out = [f"SSRN results for {query!r} (via OpenAlex, {payload.get('meta', {}).get('count', '?')} total):"]
    for i, w in enumerate(rows, 1):
        doi = w.get("doi") or ""
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in (w.get("authorships") or [])[:5]
        ]
        out.append(f"[{i}] {w.get('title') or 'Untitled'}")
        out.append(f"[{i}] URL: {doi or w.get('id', '')}")
        if any(authors):
            out.append(f"[{i}] Authors: {', '.join(a for a in authors if a)}")
        out.append(f"[{i}] Published: {w.get('publication_date', 'unknown')} | cited by {w.get('cited_by_count', 0)}")
        out.append("")
    return "\n".join(out)


# ── Tools: reading ───────────────────────────────────────────────────────────


@mcp.tool()
def read_url(url: str, with_links: bool = False, with_images: bool = False) -> str:
    """Read a webpage or PDF and return clean markdown.

    Use for any specific URL the user provides, for documentation, articles, or
    GitHub issues. Bypasses most rendering problems by using Jina's reader.

    Args:
        url: Full URL to fetch.
        with_links: Also return every hyperlink found on the page.
        with_images: Also return every image found on the page.
    """
    headers = {}
    if with_links:
        headers["X-With-Links-Summary"] = "true"
    if with_images:
        headers["X-With-Images-Summary"] = "true"

    ok, payload = _json_request(READER_URL, method="POST", body={"url": url}, headers=headers)
    if not ok:
        return f"Failed to read {url}: {payload}"

    d = payload.get("data") or {}
    parts = [
        f"Title: {d.get('title', 'Untitled')}",
        f"URL: {d.get('url', url)}",
    ]
    if d.get("publishedTime"):
        parts.append(f"Published: {d['publishedTime']}")
    parts.append("")
    parts.append(d.get("content") or d.get("text") or "(no content extracted)")
    if with_links and d.get("links"):
        parts.append("\n--- Links ---")
        parts.append(json.dumps(d["links"])[:4000])
    if with_images and d.get("images"):
        parts.append("\n--- Images ---")
        parts.append(json.dumps(d["images"])[:4000])
    return "\n".join(parts)


@mcp.tool()
def parallel_read_url(urls: list[str], with_links: bool = False) -> str:
    """Read several URLs concurrently. Preferred over repeated read_url calls.

    Args:
        urls: URLs to fetch (typically 3-5).
        with_links: Also return hyperlinks for each page.
    """
    if not urls:
        return "No URLs supplied."
    results = _fan_out(lambda u: (u, read_url(u, with_links=with_links)), urls)
    return "\n\n".join(f"═══ {u} ═══\n{body}" for u, body in results)


@mcp.tool()
def capture_screenshot_url(url: str, full_page: bool = False) -> str:
    """Capture a screenshot of a webpage and return the hosted image URL.

    Use when the user wants to SEE what a page looks like rather than read it.

    Args:
        url: Page to screenshot.
        full_page: Capture the entire scrollable page instead of the viewport.
    """
    mode = "pageshot" if full_page else "screenshot"
    ok, payload = _json_request(
        READER_URL, method="POST", body={"url": url}, headers={"X-Respond-With": mode}
    )
    if not ok:
        return f"Screenshot failed for {url}: {payload}"
    d = payload.get("data") or {}
    shot = d.get("screenshotUrl") or d.get("pageshotUrl") or ""
    return f"Title: {d.get('title', '')}\nURL: {d.get('url', url)}\nScreenshot: {shot}"


@mcp.tool()
def extract_pdf(url: str) -> str:
    """Extract layout elements (figures, tables, equations) from a PDF.

    Use when a PDF's structure matters — pulling out figures or tabular data
    rather than a flat text dump. For plain PDF text, read_url is cheaper.

    Args:
        url: URL of the PDF.
    """
    ok, payload = _json_request(
        f"{SEARCH_VIP}extract-pdf", method="POST", body={"url": url}
    )
    if not ok:
        return f"PDF extraction failed for {url}: {payload}"
    floats = payload.get("floats") or []
    if not floats:
        return f"No layout elements found in {url}."
    out = [f"Layout elements in {url} ({len(floats)} found):"]
    for i, f in enumerate(floats[:40], 1):
        out.append(f"[{i}] {f.get('type', '?')} {f.get('number', '')} — {f.get('caption', '')[:180]}")
    return "\n".join(out)


# ── Tools: search ────────────────────────────────────────────────────────────


@mcp.tool()
def search_web(query: str, num: int = 10, site: str = "", tbs: str = "",
               gl: str = "", hl: str = "") -> str:
    """Search the live web. Returns titles, URLs and descriptions.

    Use for current events, real-time information, or anything needing sources
    from the open web. Follow up with read_url for full page content.

    Args:
        query: Search terms.
        num: Results to return (capped at 20 upstream).
        site: Restrict to one domain, e.g. 'github.com'. NOTE: site-restricted
            search currently fails upstream — leave empty unless you need it.
        tbs: Time filter — qdr:h, qdr:d, qdr:w, qdr:m, qdr:y.
        gl: Country code for geo-targeting, e.g. 'us'.
        hl: UI language code, e.g. 'en'.
    """
    return _search_web(query, num, site or None, tbs or None, gl or None, hl or None)


@mcp.tool()
def parallel_search_web(queries: list[str], num: int = 10, tbs: str = "") -> str:
    """Run several web searches concurrently. Use for query variants in one pass.

    Args:
        queries: 2-5 query strings.
        num: Results per query.
        tbs: Time filter applied to every query.
    """
    if not queries:
        return "No queries supplied."
    out = _fan_out(lambda q: _search_web(q, num, None, tbs or None, None, None), queries)
    return "\n\n".join(out)


@mcp.tool()
def search_arxiv(query: str, num: int = 10, sort: str = "relevance") -> str:
    """Search arXiv for academic papers.

    Supports arXiv field syntax directly: 'cat:cs.CL', 'abs:retrieval augmented',
    'au:Vaswani', and boolean AND/OR. A bare query searches all fields.

    Args:
        query: Search terms, optionally using arXiv field prefixes.
        num: Papers to return.
        sort: 'relevance', 'date' (newest submitted), or 'updated'.
    """
    return _search_arxiv(query, num, sort)


@mcp.tool()
def parallel_search_arxiv(queries: list[str], num: int = 10, sort: str = "relevance") -> str:
    """Search arXiv for several queries concurrently.

    Args:
        queries: 2-5 query strings.
        num: Papers per query.
        sort: 'relevance', 'date', or 'updated'.
    """
    if not queries:
        return "No queries supplied."
    return "\n\n".join(_fan_out(lambda q: _search_arxiv(q, num, sort), queries))


@mcp.tool()
def search_ssrn(query: str, num: int = 10) -> str:
    """Search SSRN for economics, law, and finance working papers.

    Args:
        query: Search terms.
        num: Papers to return.
    """
    return _search_ssrn(query, num)


@mcp.tool()
def parallel_search_ssrn(queries: list[str], num: int = 10) -> str:
    """Search SSRN for several queries concurrently.

    Args:
        queries: 2-5 query strings.
        num: Papers per query.
    """
    if not queries:
        return "No queries supplied."
    return "\n\n".join(_fan_out(lambda q: _search_ssrn(q, num), queries))


@mcp.tool()
def search_bibtex(query: str, num: int = 5) -> str:
    """Find BibTeX citation entries for a paper.

    Queries DBLP first (authoritative for CS venues), then falls back to
    Semantic Scholar. Use when the user needs a LaTeX citation.

    Args:
        query: Paper title or author.
        num: Entries to return.
    """
    limit = max(1, min(num, 20))
    ok, payload = _json_request(
        f"{DBLP_API}?{urllib.parse.urlencode({'q': query, 'format': 'json', 'h': limit})}",
        auth=False,
    )
    entries = []
    if ok and isinstance(payload, dict):
        hits = (payload.get("result", {}).get("hits", {}) or {}).get("hit", []) or []
        for h in hits:
            info = h.get("info", {})
            key = info.get("key", "")
            if key:
                entries.append(
                    f"@article{{DBLP:{key},\n  title   = {{{info.get('title', '')}}},\n"
                    f"  author  = {{{info.get('authors', {}).get('author', '') if isinstance(info.get('authors'), dict) else ''}}},\n"
                    f"  venue   = {{{info.get('venue', '')}}},\n  year    = {{{info.get('year', '')}}},\n"
                    f"  url     = {{{info.get('ee', info.get('url', ''))}}}\n}}"
                )
    if entries:
        return f"BibTeX for {query!r} (DBLP):\n\n" + "\n\n".join(entries[:limit])

    ok, payload = _json_request(
        f"{S2_API}?{urllib.parse.urlencode({'query': query, 'limit': limit, 'fields': 'title,year,authors,externalIds,url'})}",
        auth=False,
    )
    if not ok:
        return f"BibTeX lookup failed for {query!r}: {payload}"
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if not rows:
        return f"No citations found for {query!r} in DBLP or Semantic Scholar."
    out = [f"BibTeX for {query!r} (Semantic Scholar):", ""]
    for p in rows:
        pid = (p.get("externalIds") or {}).get("DOI") or p.get("paperId", "")
        authors = " and ".join(a.get("name", "") for a in (p.get("authors") or [])[:8])
        out.append(
            f"@article{{{(pid or 'unknown').replace('/', '_')},\n  title  = {{{p.get('title', '')}}},\n"
            f"  author = {{{authors}}},\n  year   = {{{p.get('year', '')}}},\n"
            f"  url    = {{{p.get('url', '')}}}\n}}\n"
        )
    return "\n".join(out)


@mcp.tool()
def search_images(query: str, num: int = 5) -> str:
    """Search for images on the web.

    NOTE: image search is only available on the paid `vip` search lane. On the
    default `standard` endpoint this returns an explanatory error rather than
    results.

    Args:
        query: What to find images of.
        num: Images to return.
    """
    ok, payload = _json_request(
        SEARCH_VIP,
        method="POST",
        body={"q": query, "type": "images", "num": max(1, min(num, SEARCH_NUM_CAP))},
    )
    if not ok:
        return (
            f"Image search failed for {query!r}: {payload}\n\n"
            "Image search has no free-lane equivalent — s.jina.ai returns 503 for "
            "type='images'. This tool needs a paid Jina balance."
        )
    rows = payload.get("results") or payload.get("data") or []
    if not rows:
        return f"No images for {query!r}."
    out = [f"Images for {query!r}:"]
    for i, r in enumerate(rows[:num], 1):
        out.append(f"[{i}] {r.get('title', '')} — {r.get('imageUrl') or r.get('url', '')}")
    return "\n".join(out)


@mcp.tool()
def search_jina_blog(query: str, num: int = 5) -> str:
    """Search Jina AI's own blog and release notes.

    Requires JINA_GHOST_KEY (a Ghost Content API key) to be set.

    Args:
        query: Search terms.
        num: Posts to return.
    """
    if not GHOST_KEY:
        return (
            "search_jina_blog needs JINA_GHOST_KEY (a Ghost Content API key) in the "
            "environment. Unset — skipping. This tool is optional; nothing else needs it."
        )
    params = {"key": GHOST_KEY, "limit": max(1, min(num, 20)),
              "filter": f"title:~'{query}'", "fields": "title,url,excerpt,published_at"}
    ok, payload = _json_request(
        f"https://cms.jina.ai/ghost/api/content/posts/?{urllib.parse.urlencode(params)}",
        auth=False,
    )
    if not ok:
        return f"Blog search failed: {payload}"
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    if not posts:
        return f"No blog posts matching {query!r}."
    return "\n".join(
        f"[{i}] {p.get('title', '')} — {p.get('url', '')} ({(p.get('published_at') or '')[:10]})"
        for i, p in enumerate(posts, 1)
    )


# ── Tools: embeddings-backed utilities ───────────────────────────────────────


@mcp.tool()
def sort_by_relevance(query: str, documents: list[str], top_n: int = 0) -> str:
    """Rerank documents by semantic relevance to a query.

    Use to prioritise search results or snippets before deep reading. Cheap and
    fast — run it over a union of results from several searches.

    Args:
        query: What to rank against.
        documents: Texts (or URLs) to rank.
        top_n: Return only the top N. 0 returns all, ranked.
    """
    if not documents:
        return "No documents supplied."
    body = {"model": RERANK_MODEL, "query": query, "documents": documents}
    if top_n:
        body["top_n"] = top_n
    ok, payload = _json_request(f"{API_BASE}/v1/rerank", method="POST", body=body)
    if not ok:
        return f"Rerank failed: {payload}"
    out = []
    for r in payload.get("results", []):
        idx = r.get("index", 0)
        doc = r.get("document")
        text = doc.get("text") if isinstance(doc, dict) else (doc or documents[idx])
        out.append(f"[{r.get('relevance_score', 0):.4f}] (#{idx}) {str(text)[:300]}")
    return "\n".join(out) or "Rerank returned no results."


@mcp.tool()
def deduplicate_strings(strings: list[str], k: int = 0) -> str:
    """Pick a semantically diverse subset, dropping near-duplicates.

    Use before feeding many snippets into a synthesis step — it removes redundant
    passages and cuts token spend.

    Args:
        strings: Candidate texts.
        k: How many to keep. 0 picks roughly half.
    """
    if not strings:
        return "No strings supplied."
    if len(strings) == 1:
        return strings[0]

    keep = k if k > 0 else max(1, len(strings) // 2)
    ok, payload = _json_request(
        f"{API_BASE}/v1/embeddings",
        method="POST",
        body={"model": EMBED_MODEL, "input": strings},
    )
    if not ok:
        return f"Deduplication failed: {payload}"

    vecs = [row["embedding"] for row in payload.get("data", [])]
    if len(vecs) != len(strings):
        return f"Embedding count mismatch: got {len(vecs)} for {len(strings)} inputs."

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    # Greedy max-min diversity: repeatedly take the item furthest from those kept.
    chosen = [0]
    while len(chosen) < min(keep, len(strings)):
        best, best_score = None, 2.0
        for i in range(len(strings)):
            if i in chosen:
                continue
            worst = max(cosine(vecs[i], vecs[j]) for j in chosen)
            if worst < best_score:
                best, best_score = i, worst
        if best is None:
            break
        chosen.append(best)

    return "\n".join(f"[{i}] {strings[i][:400]}" for i in sorted(chosen))


@mcp.tool()
def classify_text(texts: list[str], labels: list[str]) -> str:
    """Zero-shot classify texts into supplied labels.

    Use for sentiment, topic tagging, or triaging a batch of documents.

    Args:
        texts: Texts to classify.
        labels: Candidate labels.
    """
    if not texts or not labels:
        return "Both texts and labels are required."
    ok, payload = _json_request(
        f"{API_BASE}/v1/classify",
        method="POST",
        body={"model": EMBED_MODEL, "input": texts, "labels": labels},
    )
    if not ok:
        return f"Classification failed: {payload}"
    out = []
    for row in payload.get("data", []):
        i = row.get("index", 0)
        src = texts[i][:90] if i < len(texts) else ""
        out.append(f"[{i}] {row.get('prediction', '?')} ({row.get('score', 0):.3f}) — {src}")
    return "\n".join(out) or "No classifications returned."


# ── Tools: local utilities ───────────────────────────────────────────────────


@mcp.tool()
def guess_datetime_url(url: str) -> str:
    """Estimate when a page was published, for freshness and credibility checks.

    Prefers the publisher's own timestamp, falling back to a date embedded in the
    URL path.

    Args:
        url: Page to date.
    """
    ok, payload = _json_request(READER_URL, method="POST", body={"url": url})
    if ok:
        published = (payload.get("data") or {}).get("publishedTime")
        if published:
            return f"URL: {url}\nBest guess: {published}\nConfidence: high (publisher timestamp)"

    m = re.search(r"/(20\d{2})[/-](\d{1,2})(?:[/-](\d{1,2}))?/", url)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), (m.group(3) or "01").zfill(2)
        return f"URL: {url}\nBest guess: {y}-{mo}-{d}\nConfidence: medium (date in URL path)"
    return f"URL: {url}\nBest guess: unknown\nConfidence: none"


@mcp.tool()
def primer() -> str:
    """Report current UTC time and this server's configuration.

    Use at session start to ground time-sensitive reasoning, or to confirm which
    search lane is active when diagnosing failures.
    """
    now = datetime.now(timezone.utc)
    lane = SEARCH_VIP if SEARCH_ENDPOINT == "vip" else SEARCH_STANDARD
    return (
        f"UTC now: {now.isoformat(timespec='seconds')}\n"
        f"Date: {now.strftime('%A, %d %B %Y')}\n"
        f"Search lane: {SEARCH_ENDPOINT} ({lane})\n"
        f"Reader: {READER_URL}\n"
        f"arXiv: {ARXIV_API} (key-less)\n"
        f"SSRN: OpenAlex source {OPENALEX_SSRN_SOURCE} (key-less)\n"
        f"Rerank model: {RERANK_MODEL} | Embed model: {EMBED_MODEL}"
    )


@mcp.tool()
def show_api_key() -> str:
    """Show the masked API key this server is using, and its wallet balance.

    Use to confirm which key is loaded and whether a failure is genuine
    exhaustion (trial_balance 0) versus an unfunded lane.
    """
    masked = f"{JINA_API_KEY[:11]}…{JINA_API_KEY[-4:]}" if len(JINA_API_KEY) > 20 else "(short key)"
    ok, payload = _json_request(
        f"https://embeddings-dashboard-api.jina.ai/api/v1/api_key/user?api_key={JINA_API_KEY}",
        auth=False,
    )
    if not ok or not isinstance(payload, dict):
        return f"Key: {masked}\nWallet: unavailable ({payload if isinstance(payload, str) else 'bad response'})"
    w = payload.get("wallet", {})

    def _num(field):
        val = w.get(field)
        return f"{val:,}" if isinstance(val, (int, float)) else "unknown"

    return (
        f"Key: {masked}\n"
        f"trial_balance:   {_num('trial_balance')}\n"
        f"regular_balance: {_num('regular_balance')}\n"
        f"Note: the vip search lane requires regular_balance > 0. Trial credits are "
        f"refused there, so a zero regular_balance with a healthy trial_balance means "
        f"'use the standard lane', NOT 'rotate the key'."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
