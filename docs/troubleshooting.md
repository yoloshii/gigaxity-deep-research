# Troubleshooting Gigaxity Deep Research

Symptom-fix lookup table for common boot and runtime errors. Find your symptom in the left column; apply the fix in the right column.

## Boot errors

| Symptom | Cause | Fix |
|---|---|---|
| `pydantic.ValidationError: RESEARCH_LLM_API_KEY` | Env var not set or empty | Set `RESEARCH_LLM_API_KEY` in `.env` or MCP `env` block |
| `ImportError: cannot import name 'mcp'` | `fastmcp` not installed | `pip install -e .` (re-install with deps) |
| `ImportError: cannot import name 'OpenAI'` from `openai` | Wrong `openai` SDK version | `pip install -U openai` |
| `ConnectionRefusedError` on first call | SearXNG not running | Start SearXNG; `curl $RESEARCH_SEARXNG_HOST/healthz` should return 200 |
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

## Search / connector errors

| Symptom | Cause | Fix |
|---|---|---|
| Empty `sources` from `discover` / `synthesize` | All connectors failed | `curl $RESEARCH_SEARXNG_HOST/search?q=test&format=json` from the orchestrator host |
| SearXNG returns HTML instead of JSON | `format=json` not enabled in SearXNG settings | Edit `searxng/settings.yml`, set `search.formats: [html, json]`, restart |
| Tavily 401 | Bad API key | Regenerate at https://app.tavily.com |
| LinkUp 403 | Free tier quota exhausted | Upgrade or remove `RESEARCH_LINKUP_API_KEY` to disable |
| Some queries return only one engine's results | SearXNG engines disabled | Edit SearXNG `settings.yml` engines section, ensure `disabled: false` for the ones you want |

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
| Per-request latency uneven (hosted endpoints) | Provider routing across multiple backends | Pin a specific provider via the provider's model-path syntax (e.g. `alibaba/tongyi-deepresearch-30b-a3b:openrouter/auto` on OpenRouter) |

## Local inference (default on this branch)

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectionError` on every call | Model server not running on `RESEARCH_LLM_API_BASE` | Start vLLM/SGLang/llama.cpp; verify with `curl $RESEARCH_LLM_API_BASE/models` |
| `Unauthorized` from model server | Bearer token mismatch | Set `RESEARCH_LLM_API_KEY` to whatever your model server expects; non-empty placeholder for open endpoints |
| OOM at model-server startup | Model larger than VRAM | Switch to a quantized variant (AWQ, INT4) or smaller model |
| Slow first request after model load | Prompt-eval cold-start | Send a warmup request after the model server reports loaded |
| Inconsistent output quality | Wrong template applied to reasoning model | For Tongyi/DeepSeek-R1, ensure the model server uses the chat template that exposes `<thinking>...</thinking>` tags |

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
