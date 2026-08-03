# Troubleshooting Gigaxity Deep Research

Symptom-fix lookup table for common boot and runtime errors. Find your symptom in the left column; apply the fix in the right column.

## Boot errors

| Symptom | Cause | Fix |
|---|---|---|
| `pydantic.ValidationError: RESEARCH_LLM_API_KEY` | Env var not set or empty | Set `RESEARCH_LLM_API_KEY` in `.env` or MCP `env` block |
| `ImportError: cannot import name 'mcp'` | `fastmcp` not installed | `pip install -e .` (re-install with deps) |
| `ImportError: cannot import name 'OpenAI'` from `openai` | Wrong `openai` SDK version | `pip install -U openai` |
| `ConnectionRefusedError` on first call | SearXNG not running | Start SearXNG; `curl $RESEARCH_SEARXNG_HOST/healthz` should return 200. REST mode: `GET /api/v1/health/connectors` probes every connector at once |
| MCP server boots but Claude Code shows no tools | `command` in `~/.claude.json` points at wrong Python | Use absolute path to venv's Python |
| MCP server hangs at startup | SearXNG host unreachable from inside the venv | `curl` from a fresh shell — DNS or firewall issue |
| `EnvironmentError: RESEARCH_SEARXNG_HOST not reachable` | Localhost binding mismatch | If using Docker for both, use `host.docker.internal` or container DNS |

## LLM endpoint errors

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectionError` / `APIConnectionError` on every call | Configured `RESEARCH_LLM_API_BASE` not reachable | For local servers, start vLLM/SGLang/llama.cpp; `curl $RESEARCH_LLM_API_BASE/models` should return 200. For hosted services, check DNS/firewall to the upstream. |
| 401 from LLM endpoint | Invalid bearer token | Match `RESEARCH_LLM_API_KEY` to what your endpoint expects (real key for hosted; placeholder for open local servers) |
| 402 from hosted endpoint (e.g. OpenRouter) | Account out of credits | Top up on the provider dashboard |
| 429 rate limit | Quota or per-tenant limit hit | Reduce `RESEARCH_DEFAULT_TOP_K`, switch to `fast` preset, or wait the indicated retry-after |
| `Model not found` 400 | Model slug typo or model not loaded | Use the exact slug your server registered (vLLM logs the full path on load); check `curl $RESEARCH_LLM_API_BASE/models` |
| `Context length exceeded` | Sources too large for model context | Lower `RESEARCH_DEFAULT_TOP_K`, enable RCS via `synthesize/p1` endpoint, or shorten source content |
| Empty completions | Model not loaded yet, or rate-limited | vLLM/SGLang takes 30–120 s to load 30B; for hosted services, check the provider dashboard |
| Inconsistent quality on repeated calls | Temperature too high | Lower `RESEARCH_LLM_TEMPERATURE` to 0.3–0.5 |
| Response starts with `# Synthesis verification FAILED` | LLM produced degraded synthesis output. The header lists which condition(s) hit: empty completion, a reasoning trace returned in place of an answer, truncation by `max_tokens` even after a one-shot retry at the ceiling, a failed contributing sub-call, or zero citations when sources exist. | Lower `RESEARCH_DEFAULT_TOP_K`, switch preset to `fast`, verify the model is fully loaded and not rate-limited; raise `RESEARCH_LLM_MAX_TOKENS` if truncation persists on reasoning models. Hard-failed outputs are not cached — the next call re-runs. |
| Response ends with `*Verification notes: partial citation coverage...*` | Soft warning: model answered but cited fewer sources than provided. Output is usable; the note flags the coverage gap. | Inspect the answer for claims not tied to provided sources. Acceptable on heterogenous source sets where some inputs are off-topic. |

## Search / connector errors

| Symptom | Cause | Fix |
|---|---|---|
| Empty `sources` from `discover` / `synthesize` | All connectors failed | `curl $RESEARCH_SEARXNG_HOST/search?q=test&format=json` from the orchestrator host |
| SearXNG returns HTML instead of JSON | `format=json` not enabled in SearXNG settings | Edit `searxng/settings.yml`, set `search.formats: [html, json]`, restart |
| Tavily 401 | Bad API key | Regenerate at https://app.tavily.com |
| LinkUp 403 | Free tier quota exhausted | Upgrade or remove `RESEARCH_LINKUP_API_KEY` to disable |
| Some queries return only one engine's results | SearXNG engines disabled | Two places must agree in the bundled config: the engine must be listed under `use_default_settings.engines.keep_only` **and** have `disabled: false` in the `engines:` block. `keep_only` is an allow-list — an engine absent from it does not exist, and setting `disabled: false` alone will not bring it back. SearXNG does not warn about an unknown name there; it silently drops it |
| SearXNG returns few or irrelevant results, but HTTP 200 and no error | Upstream engines are CAPTCHA'd, rate-limited or serving bot-block pages. A degraded SearXNG **never** signals failure in the status code | `curl -s "$RESEARCH_SEARXNG_HOST/search?q=test&format=json" \| python3 -c 'import sys,json;print(json.load(sys.stdin)["unresponsive_engines"])'` — entries like `["brave","Suspended: too many requests"]` or `["duckduckgo","CAPTCHA"]` name the failures. Scraped engines trip after surprisingly little automated load (single-digit queries per minute is enough for some), and `search.suspended_times` in `settings.yml` controls how long each block lasts — up to 2h for `cf_SearxEngineCaptcha`. Pace the caller, or move a lane to an official API (see "Durable lanes" in `companions/searxng/settings.yml.example`) |
| An engine returns confident but off-topic results (homepages, dictionary entries, unrelated forum threads) | That engine is serving its blocked-client fallback page rather than search results, at HTTP 200 | Check which engine by tagging results: `curl -s "$RESEARCH_SEARXNG_HOST/search?q=<query>&format=json" \| python3 -c 'import sys,json;[print(r.get("engines"),r["title"][:70]) for r in json.load(sys.stdin)["results"][:10]]'`. Disable the offending engine — RRF fuses on rank, so junk at any weight displaces good results and lowering its weight does not help |
| `search` / `research` / `discover` trailer shows `configured: ['searxng']` only (Tavily + LinkUp missing) | `RESEARCH_TAVILY_API_KEY` and/or `RESEARCH_LINKUP_API_KEY` not set in MCP env | Set both env vars in the MCP `env` block (`~/.claude.json` for Claude Code; equivalent for other harnesses) or in the `.env` file. **Restart the MCP server** for new env vars to take effect — env is read at process startup. Searxng-only mode is functional but lower-coverage. |
| `search` trailer shows `from ['searxng']` but `configured: ['searxng', 'tavily', 'linkup']` (all 3 configured, only 1 returned) | Tavily / LinkUp connector hit quota, rate limit, or upstream error (logged to stderr) | Check the MCP's stderr log for `Tavily search error` / `LinkUp search error`. Verify quota/balance on the connector's dashboard. Connectors that error are silently absorbed by the aggregator — only the successful contributors appear in the trailer's first list. |

## MCP integration errors

| Symptom | Cause | Fix |
|---|---|---|
| `/mcp` in Claude Code shows red dot | Server crashed or never booted | Check `~/.claude.json` `command` and `args` — try running them by hand |
| Server boots but tools are missing | FastMCP version mismatch | `pip install -U fastmcp` |
| Tools appear under wrong alias | Alias key in `~/.claude.json` differs from expected | Either rename the JSON key or update agent prompts to use the new alias |
| Per-request `api_key` parameter ignored | Server boot predates this feature | Pull latest `local-inference`; rebuild venv |
| MCP responses truncated | Output > MCP message size limit | Lower `RESEARCH_LLM_MAX_TOKENS`; use `fast` preset for shorter outputs |

## REST API errors

| Symptom | Cause | Fix |
|---|---|---|
| 422 `validation_error` on POST | Request body shape mismatch | Check schema in [reference/rest-api.md](reference/rest-api.md); `pydantic` reports the bad field |
| `X-LLM-Api-Key` header ignored | Header name typo | Exact name is `X-LLM-Api-Key` (the alias in `routes.py`); HTTP makes it case-insensitive but typos still fail |
| Connection drops on long synthesize calls | Reverse proxy timeout | Increase timeout on the proxy; FastAPI itself doesn't time out short of `RESEARCH_LLM_TIMEOUT` |
| 500 with no useful error | Unhandled exception | Check server logs (`docker compose logs -f` or stdout); enable `--log-level debug` on uvicorn |

## Performance issues

| Symptom | Cause | Fix |
|---|---|---|
| `synthesize` takes > 30 s | Quality gate enabled with many sources | Use `fast` preset, lower `RESEARCH_DEFAULT_TOP_K` |
| `discover` slow | Multiple search engines + decomposition | Disable LinkUp/Tavily by clearing their keys; reduce engine list in `RESEARCH_SEARXNG_ENGINES` |
| First call after long idle is slow | LLM endpoint cold-start (model unload, hosted-provider routing) | Send a warmup `ask` call before traffic; for vLLM/SGLang, keep the server warm |
| High RAM usage | Large source content + RCS off | Enable RCS via `/synthesize/p1` endpoint |
| Per-request latency uneven (hosted endpoints) | Provider routing across multiple backends | Pin a specific provider via the provider's model-path syntax (e.g. `qwen/qwen3-30b-a3b-thinking-2507:openrouter/auto` on OpenRouter) |
| First `synthesize` call after upgrade is slow | `SYNTH_CACHE_VERSION` was bumped (cache key now includes the effective output budget plus source order), invalidating prior entries. | One-time cost; subsequent calls re-cache. No action required. |
| **Identical repeated requests are never fast — every call costs a full LLM round-trip** | The result cache is not writable. In Docker this is near-certain: the `research_cache` **named volume is created root-owned**, while the container runs as `researcher` (uid 1000), so every write raises `PermissionError`. Nothing fails and no result is wrong — you simply pay for every repeat. | See "Cache never hits" below. |

## Cache never hits (Docker)

**Symptom:** two identical requests each take the full synthesis time. `/tmp/research_cache` inside the
container stays empty forever.

Confirm it in one command — if this prints a `PermissionError`, that is the whole problem:

```bash
docker compose exec deepresearch python -c \
  "from pathlib import Path; Path('/tmp/research_cache/.probe').write_text('x'); print('writable')"
```

**Cause.** `docker-compose.yml` mounts a named volume at `/tmp/research_cache`. Docker creates a named
volume **root-owned** unless the image already has a directory at that path to seed ownership from. The
container runs as uid 1000, so it cannot write. Releases before v0.11.0 also swallowed the error
entirely (`except (TypeError, OSError): pass`), so nothing appeared in the logs — the cache looked like
it was working while never storing a single entry.

**Fix — new deployments.** Nothing to do. v0.11.0's Dockerfile pre-creates the directory owned by
`researcher`, and a fresh volume inherits that ownership.

**Fix — existing deployments.** The image change cannot alter a volume that already exists. Either
recreate it (simplest; the cache is ephemeral by design, so there is nothing to preserve):

```bash
docker compose down
docker volume rm "$(basename "$PWD")_research_cache"   # or: docker volume ls | grep research_cache
docker compose up -d
```

or chown it in place, without downtime:

```bash
docker volume inspect <project>_research_cache --format '{{.Mountpoint}}'   # → /var/lib/docker/volumes/.../\_data
sudo chown -R 1000:1000 /var/lib/docker/volumes/<project>_research_cache/_data
```

**Verify** — the second call should return in milliseconds:

```bash
for i in 1 2; do
  curl -s -o /dev/null -w "call $i: %{time_total}s\n" -X POST localhost:8000/api/v1/synthesize \
    -H 'Content-Type: application/json' \
    -d '{"query":"test","sources":[{"origin":"web","url":"https://e/1","title":"T","content":"C","source_type":"article"}],"max_tokens":500}'
done
```

From v0.11.0 an unwritable cache also logs a one-time `WARNING` naming the cause and the fix, so this
stops being invisible.

> Structurally-failed syntheses are deliberately **never** cached — a `# Synthesis verification FAILED`
> response re-runs on the next call rather than being served from cache for the full TTL.

## Local inference (default on this branch)

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectionError` on every call | Model server not running on `RESEARCH_LLM_API_BASE` | Start vLLM/SGLang/llama.cpp; verify with `curl $RESEARCH_LLM_API_BASE/models` |
| `Unauthorized` from model server | Bearer token mismatch | Set `RESEARCH_LLM_API_KEY` to whatever your model server expects; non-empty placeholder for open endpoints |
| OOM at model-server startup | Model larger than VRAM | Switch to a quantized variant (AWQ, INT4) or smaller model |
| Slow first request after model load | Prompt-eval cold-start | Send a warmup request after the model server reports loaded |
| Inconsistent output quality | Wrong template applied to reasoning model | For Qwen3-Thinking/DeepSeek-R1, ensure the model server uses the chat template that exposes `<thinking>...</thinking>` tags |

## Multi-tenant edge cases

| Symptom | Cause | Fix |
|---|---|---|
| One user's request bills another's account | Per-request key not extracted | Verify the request includes `X-LLM-Api-Key` header (REST) or `api_key` parameter (MCP) |
| Per-request key appears in server logs | Default uvicorn access log | Strip the header at the reverse proxy (see [setup-rest.md](guides/setup-rest.md)) |
| Per-request key passes auth but answers come from owner key's model preference | Bug in client extraction order | Pull latest `local-inference`; bug fixed in v0.1.x |

## Where to file issues

Stuck on something not in this table? Open a [bug report](https://github.com/yoloshii/gigaxity-deep-research/issues/new?template=bug-report.yml) with:

- Output of `/api/v1/health`
- Output of `pip show fastmcp openai pydantic-settings`
- Full error traceback
- The exact request you sent (redact API keys)

For security-sensitive findings, use [private vulnerability reporting](https://github.com/yoloshii/gigaxity-deep-research/security/advisories/new) instead — see [SECURITY.md](../SECURITY.md).
