# Custom social-first retrievers for gptr-mcp (opt-in)

The `gptr-mcp` companion clones **vanilla** upstream GPT Researcher, which ships
a fixed set of retrievers (Tavily, Google, Bing, SearXNG, …). The two retrievers
the rest of this stack's docs reference — **`social_openai`** and
**`twitterapi`** — are **not** in a vanilla install. They live here, in this
repo, as a first-party add-on you enable once.

This is deliberately **opt-in**: out of the box the companion runs as a stock
GPT Researcher MCP (`RETRIEVER=tavily`). Follow this guide to switch on the
social-first retrievers. Budget ~2 minutes.

> **Why not bundle them into the upstream clone automatically?** The companion's
> whole design is *clone-upstream-at-install, don't vendor* — so you always get
> the latest GPT Researcher without version drift (see
> [README.md](README.md#why-not-bundle-source)). These retrievers are *our*
> code, not upstream's, so they ship here and you graft them onto your clone.

---

## What you get

| Retriever | Backs | How it works |
|---|---|---|
| `social_openai` | Reddit + YouTube (and any domain you list) | OpenAI's `web_search` tool with an `allowed_domains` filter. Reddit threads are resolved to comment text **off-IP** via the [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift) archive API — reddit.com is never contacted directly (it 403s scrapers and rate-limits by IP). |
| `twitterapi` | X / Twitter | Native [TwitterAPI.io](https://twitterapi.io) `advanced_search` — returns full tweet objects (text + engagement + author) in one call and hands them to the conductor as pre-fetched content. x.com is never scraped from your IP. |

Both retrievers set `raw_content` on every result, so GPT Researcher uses the
fetched text directly and never scrapes the social URL. Both degrade safely:
on a missing key, error, or budget cap they return **nothing** rather than a
bare scrapeable href.

### A note on the Arctic Shift archive (third-party)

`social_openai`'s Reddit path reads the
**[Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift) archive** — a
free, volunteer-run Reddit data archive that is **not affiliated with Reddit or
this project**. A few things to know:

- **Be considerate — it's someone else's free infrastructure.** The resolver
  ships polite defaults: a single-flight lock, a 0.6 s minimum interval between
  calls, per-thread caching, and a hard 100-comment fetch cap. Don't lower
  `ARCTIC_SHIFT_MIN_INTERVAL` or raise `ARCTIC_SHIFT_FETCH_LIMIT` aggressively,
  especially under concurrent research load. If you have heavy or sustained
  Reddit needs, stand up your own backend (see the last bullet) rather than
  leaning on the public instance.
- **It can rate-limit or be down.** On a 429/5xx or timeout the resolver retries
  once, then degrades safely — the Reddit result falls back to the search
  snippet or is dropped (reddit.com is never scraped). An outage lowers quality;
  it never breaks a run.
- **It's an archive — interpret results accordingly.** Comment *text* is
  near-real-time, but **engagement scores lag (~36 h)** on fresh threads. The
  resolver therefore preserves Arctic Shift's returned order instead of
  re-ranking by score, and shows score as metadata only. Agents consuming these
  results should not treat Reddit scores as current.
- **You can turn it off, or swap the backend.** `ARCTIC_SHIFT_ENABLED=false`
  skips the archive entirely (Reddit results then fall back to the OpenAI search
  snippet, or drop). The resolver is backend-pluggable — `RedditCommentResolver`
  runs an ordered list of backends, so a self-hosted or paid off-IP backend can
  be inserted ahead of the snippet fallback without touching the retriever.

---

## Prerequisites

1. **The companion is installed.** You've run `./install.sh` and have a
   `gptr-mcp-source/` directory with a `.venv`. If not, do that first
   ([README.md](README.md#install)).
2. **GPT Researcher ≥ 3.5.0.** These retrievers rely on the conductor's
   `raw_content > 100` pre-fetch semantics introduced upstream in commit
   `f1e51ebe`, first released in **v3.5.0**. On older versions the
   "never scrape the social URL" guarantee is not validated — pin to v3.5.0+.
   This guide pins the library clone to `v3.5.0`.
3. **API keys** for whatever you turn on:
   - `OPENAI_API_KEY` — required by `social_openai` (and by GPT Researcher's LLM).
   - `TWITTERAPI_IO_KEY` — required by `twitterapi`. Paid; get one at
     <https://twitterapi.io> (roughly $0.10–0.15 per 1,000 tweets at time of
     writing — check current pricing). Leave it unset and `twitterapi` is a
     silent no-op (safe).

---

## Enable it — recommended path (editable library clone)

This clones the GPT Researcher **library** at the pinned tag, grafts the
retrievers on, and installs it editable into the companion's venv. It survives
`gptr-mcp` server updates and keeps the change in a real git repo you can
inspect and re-sync.

```bash
# Run from this directory: companions/gptr-mcp/
COMPANION_DIR="$(pwd)"
GPTR_MCP_SRC="/absolute/path/to/gptr-mcp-source"   # where install.sh cloned the server

# 1. Clone the GPT Researcher library at the pinned tag (sibling of gptr-mcp-source)
git clone https://github.com/assafelovic/gpt-researcher.git ../../../gpt-researcher-source
cd ../../../gpt-researcher-source
git checkout v3.5.0

# 2. Drop in the two first-party retriever packages
cp -r "$COMPANION_DIR/retrievers/social_openai" gpt_researcher/retrievers/
cp -r "$COMPANION_DIR/retrievers/twitterapi"    gpt_researcher/retrievers/

# 3. Wire them into the registry (adds 2 lines to each of 3 files)
git apply "$COMPANION_DIR/retrievers/social-retrievers.patch"
git diff --stat        # optional: confirm the 3 registry files changed

# 4. Install the patched library into the companion venv (overrides the pip-pulled copy).
#    --no-deps because v3.5.0's deps are already satisfied by the gptr-mcp install.
"$GPTR_MCP_SRC/.venv/bin/pip" install -e . --no-deps

# 5. Verify the retrievers resolve (see "Verify" below)
"$GPTR_MCP_SRC/.venv/bin/python" - <<'PY'
from gpt_researcher.actions.retriever import get_retriever
assert get_retriever("social_openai"), "social_openai did NOT register"
assert get_retriever("twitterapi"), "twitterapi did NOT register"
print("✓ social_openai + twitterapi registered")
PY
```

---

## Enable it — quick path (patch the installed package)

Faster (no second clone), but it edits files inside the venv's `site-packages`,
so **`pip install --upgrade gpt-researcher` wipes it** — you'd re-run this. Use
the editable-clone path above if you expect to update.

```bash
COMPANION_DIR="/absolute/path/to/gigaxity-deep-research/companions/gptr-mcp"
GPTR_MCP_SRC="/absolute/path/to/gptr-mcp-source"

# Locate the installed package and confirm the version floor
PKG="$("$GPTR_MCP_SRC/.venv/bin/python" -c 'import gpt_researcher,os;print(os.path.dirname(gpt_researcher.__file__))')"
"$GPTR_MCP_SRC/.venv/bin/pip" show gpt-researcher | grep -i '^Version:'   # must be >= 3.5.0

# Copy the packages in
cp -r "$COMPANION_DIR/retrievers/social_openai" "$PKG/retrievers/"
cp -r "$COMPANION_DIR/retrievers/twitterapi"    "$PKG/retrievers/"

# Apply the registry patch from the site-packages root (-p1 strips the leading a/)
( cd "$(dirname "$PKG")" && patch -p1 < "$COMPANION_DIR/retrievers/social-retrievers.patch" )

# Verify
"$GPTR_MCP_SRC/.venv/bin/python" -c "from gpt_researcher.actions.retriever import get_retriever; assert get_retriever('social_openai') and get_retriever('twitterapi'); print('✓ registered')"
```

---

## The registry change (for reference / manual application)

The patch touches three files and adds **two lines to each**. If you'd rather
not use the patch file, apply these by hand to the GPT Researcher library:

**`gpt_researcher/retrievers/__init__.py`** — add the imports and `__all__` entries:

```python
from .social_openai.social_openai import SocialOpenAIRetriever
from .twitterapi.twitterapi import TwitterAPISearch
# ... and add "SocialOpenAIRetriever", "TwitterAPISearch" to the __all__ list
```

**`gpt_researcher/retrievers/utils.py`** — add to `VALID_RETRIEVERS`:

```python
    "social_openai",
    "twitterapi",
```

**`gpt_researcher/actions/retriever.py`** — add two `case` branches to `get_retriever()`:

```python
        case "social_openai":
            from gpt_researcher.retrievers import SocialOpenAIRetriever
            return SocialOpenAIRetriever

        case "twitterapi":
            from gpt_researcher.retrievers import TwitterAPISearch
            return TwitterAPISearch
```

The full unified diff is in [`retrievers/social-retrievers.patch`](retrievers/social-retrievers.patch).

---

## Configure

Once the retrievers are registered, point `RETRIEVER` at them. Recommended
full social-first config (Reddit + YouTube via `social_openai`, X via native
`twitterapi`):

```json
"gptr-mcp": {
  "type": "stdio",
  "command": "/absolute/path/to/gptr-mcp-source/.venv/bin/python",
  "args": ["/absolute/path/to/gptr-mcp-source/server.py"],
  "cwd": "/absolute/path/to/gptr-mcp-source",
  "env": {
    "OPENAI_API_KEY": "your-openai-api-key-placeholder",
    "TAVILY_API_KEY": "your-tavily-api-key-placeholder",
    "TWITTERAPI_IO_KEY": "your-twitterapi-io-key-placeholder",
    "RETRIEVER": "social_openai,twitterapi,tavily",
    "SOCIAL_OPENAI_DOMAINS": "reddit.com,youtube.com",
    "SOCIAL_OPENAI_MODEL": "gpt-4o",
    "FAST_LLM": "openai:gpt-4o-mini",
    "SMART_LLM": "openai:gpt-4o",
    "STRATEGIC_LLM": "openai:gpt-4o-mini"
  }
}
```

Note `x.com` is **dropped** from `SOCIAL_OPENAI_DOMAINS` here — X is handled by
the native `twitterapi` retriever, which returns richer data than an OpenAI
web-search over x.com. Don't list X in both.

**No paid X key?** Drop `twitterapi` and let `social_openai` cover X via OpenAI
web-search (lower fidelity, no engagement metrics):

```jsonc
"RETRIEVER": "social_openai,tavily",
"SOCIAL_OPENAI_DOMAINS": "reddit.com,x.com,youtube.com",
// omit TWITTERAPI_IO_KEY
```

### Environment variables

**`social_openai`**

| Var | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | OpenAI key. |
| `SOCIAL_OPENAI_DOMAINS` | `reddit.com` | Comma-separated domains the web-search is filtered to. |
| `SOCIAL_OPENAI_MODEL` | `gpt-4o` | Must be `gpt-4o` or higher — `gpt-4o-mini` does **not** support the `filters` parameter in `web_search`. |

**Arctic Shift (Reddit off-IP resolver, used by `social_openai`)**

| Var | Default | Purpose |
|---|---|---|
| `ARCTIC_SHIFT_ENABLED` | `true` | Set `false` to skip the archive call (Reddit results then fall back to the search snippet, or drop). |
| `ARCTIC_SHIFT_BASE_URL` | `https://arctic-shift.photon-reddit.com/api` | Host-allowlisted to `*.photon-reddit.com`. |
| `ARCTIC_SHIFT_FETCH_LIMIT` | `100` | Comments fetched per thread (API caps at 100). |
| `ARCTIC_SHIFT_MAX_COMMENTS` | `30` | Comments rendered into `raw_content`. |
| `ARCTIC_SHIFT_MAX_CHARS` | `6000` | Cap on a thread's rendered text. |
| `ARCTIC_SHIFT_TIMEOUT` | `15` | Seconds. |
| `ARCTIC_SHIFT_MIN_INTERVAL` | `0.6` | Min seconds between archive calls (polite to the volunteer API). |

**`twitterapi`**

| Var | Default | Purpose |
|---|---|---|
| `TWITTERAPI_IO_KEY` | *(required; else no-op)* | TwitterAPI.io key. |
| `TWITTERAPI_QUERY_TYPE` | `Top` | `Top` (engagement-ranked) or `Latest`. |
| `TWITTERAPI_MAX_PAGES_PER_QUERY` | `1` | Search pages per sub-query (each page is a paid call). |
| `TWITTERAPI_MAX_SEARCH_CALLS_PER_QUERY` | `1` | Hard cap on search calls per sub-query. |
| `TWITTERAPI_MAX_CALLS_PER_WINDOW` | `60` | Process-wide spend ceiling per rolling window. |
| `TWITTERAPI_WINDOW_SECONDS` | `300` | Rolling-window length for the spend ceiling. |
| `TWITTERAPI_MIN_INTERVAL` | `0.5` | Min seconds between calls. |
| `TWITTERAPI_REPLY_TOP_K` | `0` | `>0` enriches the top-K tweets with their replies (extra paid calls). |
| `TWITTERAPI_FILTER_RETWEETS` | `true` | Appends `-filter:retweets` to the query. |
| `TWITTERAPI_QUERY_LANG` | *(empty)* | Optional `lang:` filter. |
| `TWITTERAPI_MAX_CHARS` | `4000` | Cap on a tweet/thread's rendered text. |

`twitterapi` is read-only by construction: requests are host-allowlisted to
`api.twitterapi.io` and path-allowlisted to four read endpoints. No
`login_cookie` is ever sent, so it can't act on an account.

---

## Verify it's working

**1. The retrievers register** (the load-bearing check — see the footgun below):

```bash
/absolute/path/to/gptr-mcp-source/.venv/bin/python -c \
  "from gpt_researcher.actions.retriever import get_retriever; \
   assert get_retriever('social_openai') and get_retriever('twitterapi'); \
   print('✓ registered')"
```

**2. Live smoke** — restart Claude Code (or your harness), then call
`mcp__gptr-mcp__quick_search` with a social query (e.g. *"what do people on
Reddit think about <topic>"*). You should get Reddit comment text and, with a
TwitterAPI key, tweets — not bare links.

**3. (optional) Unit tests** — each package ships its own tests
(`test_reddit_resolver.py`, `test_twitterapi.py`). With `pytest` in the venv:

```bash
/absolute/path/to/gptr-mcp-source/.venv/bin/python -m pytest \
  gpt_researcher/retrievers/social_openai gpt_researcher/retrievers/twitterapi
```

### ⚠️ The silent-fallback footgun

GPT Researcher resolves retrievers with `get_retriever(name) or get_default_retriever()`.
If a name **doesn't register** (patch not applied, wrong venv, version too old),
it **silently falls back to Tavily** — no error, no warning, just zero social
results. So `RETRIEVER=social_openai,twitterapi,tavily` on an un-patched install
quietly becomes "Tavily, three times." **Always run check #1 above** after
enabling; if it asserts cleanly, the fallback can't bite you.

---

## Updating / re-syncing

- **Editable-clone path:** to move to a newer GPT Researcher, `cd` into
  `gpt-researcher-source`, `git fetch`, check out the new tag, re-apply the
  patch if it doesn't carry forward (`git apply` will tell you), and re-run
  `pip install -e . --no-deps`. Re-run verify check #1.
- **Patched-package path:** any `pip install --upgrade gpt-researcher` removes
  the retrievers — re-run the quick-path steps after upgrading. (This is the
  reason the editable-clone path is recommended.)
- **If the patch fails to apply** on a newer version, the three registry files
  were refactored upstream — apply the change by hand from
  [the reference section above](#the-registry-change-for-reference--manual-application);
  it's two lines per file.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Social queries return generic web results, no Reddit/X | Retrievers didn't register → silent Tavily fallback | Run verify check #1; re-apply the patch in the **correct venv**. |
| `social_openai` errors on `filters` | `SOCIAL_OPENAI_MODEL=gpt-4o-mini` | Use `gpt-4o` or higher — mini doesn't support `web_search` domain filters. |
| No X results despite `twitterapi` in `RETRIEVER` | `TWITTERAPI_IO_KEY` unset, or budget window exhausted | Set the key; check `TWITTERAPI_MAX_CALLS_PER_WINDOW`. Unset key = intentional silent no-op. |
| Reddit results thin or missing | Arctic Shift unreachable / thread not archived | Confirmed safe — Reddit is never scraped directly; thin threads fall back to the search snippet or drop. Check `ARCTIC_SHIFT_ENABLED`. |
| Patch `git apply` fails | gpt-researcher not at v3.5.0, or upstream refactored the files | Check out `v3.5.0`, or apply the 2-lines-per-file change manually. |

---

## License

These retrievers are MIT (same as this repo and upstream `gptr-mcp` /
`gpt-researcher`) — compatible to redistribute and modify.
