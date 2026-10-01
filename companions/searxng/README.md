# SearXNG companion

A one-command Docker setup for the SearXNG search aggregator that the parent project uses as its primary search source.

This companion **does not vendor SearXNG source code** — SearXNG is an [AGPL-3.0 project](https://github.com/searxng/searxng) maintained separately. We bundle only a working `docker-compose.yml` and a `settings.yml.example` tuned for use as a JSON API backend.

The included `settings.yml.example` carries a curated allow-list organised into categories, with engine weights and timeouts, re-validated against upstream SearXNG 2026.9.30 on 2026-09-30. It avoids the most common stand-up gotchas: JSON format disabled, Google returning CAPTCHA on aggregator traffic, and Cloudflare-blocked engines wedging the result fan-in. Adjust per your jurisdiction — engines blocked from one network may work fine from another.

| Category | Engines | Used by the parent project |
|---|---|---|
| `general` | DuckDuckGo, Brave, Wikipedia, Wikidata | every fused search |
| `science` | arXiv, Google Scholar, Semantic Scholar, OpenAlex, PubMed, Crossref | vertical lane (`academic` focus or keyword match) and `vertical_search` |
| `it` | Stack Overflow, Ask Ubuntu, Super User, GitHub, Hacker News | vertical lane (`debugging` / `documentation` focus or keyword match) and `vertical_search` |
| `videos` | YouTube | vertical lane (`tutorial` focus or keyword match) and `vertical_search` |
| `docs` | MDN, Microsoft Learn, Arch Linux wiki | `vertical_search` only |
| `packages` | PyPI, npm, crates.io, pkg.go.dev, Docker Hub, Hugging Face | `vertical_search` only |

Four things in that file are load-bearing and easy to get wrong if you write your own:

- **It uses `use_default_settings.engines.keep_only`, not a bare `use_default_settings: true`.** With the bare form, an `engines:` block does not define your engine set — upstream merges it into the ~250 stock engines *by name*, so every engine you don't mention keeps its default state. A curated-looking list then runs alongside ~79 unaudited default-enabled engines, several of which sit in the `general` category. `keep_only` makes it an actual allow-list.
- **A degraded SearXNG returns HTTP 200, not an error.** When upstream engines CAPTCHA or start serving bot-block pages, results silently collapse in quality with a success status. The `unresponsive_engines` field in the JSON response is the signal to monitor; see the health-check section at the bottom of `settings.yml.example`.
- **Categories are the routing contract.** The parent project sends `categories=` and, by default, no `engines=` list — an explicit engines list overrides the file's `disabled:` flags. `docs` and `packages` are custom categories defined only in this file. SearXNG answers a category it does not define from its default categories with no error, so the parent project reads the instance's `/config`, skips any category without an enabled engine, and keeps only results that an engine of the requested category returned. Do not add `categories` to `preferences.lock`: a locked instance ignores the requested category, and every category search then comes back empty.
- **The image version matters.** From 2026.9.x the image sends outgoing requests through curl_cffi with browser TLS impersonation (earlier images used httpx). On a residential test connection the 2026.8.1 image drew a DuckDuckGo CAPTCHA and a Brave rate limit on the first query, while 2026.9.30 returned results from both — same IP, same query. If your general category returns nothing, update the image before blaming your IP.

## Quick start

```bash
cd companions/searxng
cp settings.yml.example settings.yml

# Set a real secret_key in settings.yml before exposing the instance
# (defaults to a placeholder; safe for localhost only)

docker compose up -d
```

Verify:

```bash
curl http://localhost:8888/healthz
# OK

curl 'http://localhost:8888/search?q=test&format=json' | head
# JSON response with results array

curl 'http://localhost:8888/search?q=test&format=json&categories=science' | head
# results from the science engines — a category the instance lacks is silently answered from its defaults

curl -s 'http://localhost:8888/config' | head -c 300
# the parent project reads this to see which categories have an enabled engine
```

If the JSON test returns HTML, the JSON format isn't enabled — confirm `settings.yml` has `formats: [html, json]` under the `search:` section, then `docker compose restart`.

## Wire to the parent project

In the parent project's `.env`:

```bash
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

## Why bundle docker-compose, not source?

- SearXNG is a full project (~50K lines, separate maintainers), not a library — we're consumers, not redistributors
- The Docker image at `searxng/searxng:latest` is the upstream-recommended deployment path
- Bundling the compose file + settings template gives users one-command setup without coupling our release cycle to SearXNG's

## License notes

The Docker image and SearXNG source are AGPL-3.0. **Running** SearXNG as a network service alongside your own MIT-licensed code is fine — AGPL-3.0 only kicks in if you modify SearXNG itself and serve users with the modified version.

The compose file and the settings template in this directory are MIT (same as the parent repo).

## Production hardening

The defaults in `settings.yml.example` are tuned for **localhost development**. If exposing to a network:

1. Generate a real `secret_key`: `openssl rand -hex 32` and replace the placeholder
2. Set `limiter: true` to enable rate limiting
3. Put SearXNG behind a TLS-terminating reverse proxy (nginx, Caddy, Traefik)
4. Restrict the listening interface to localhost on the docker-compose, and let the reverse proxy handle external access
5. Review `engines:` and disable any that hit rate limits in your environment
