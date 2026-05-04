# SearXNG companion

A one-command Docker setup for the SearXNG search aggregator that the parent project uses as its primary search source.

This companion **does not vendor SearXNG source code** — SearXNG is an [AGPL-3.0 project](https://github.com/searxng/searxng) maintained separately. We bundle only a working `docker-compose.yml` and a `settings.yml.example` tuned for use as a JSON API backend.

The included `settings.yml.example` carries engine weights, timeouts, and a curated enable/disable list validated against a long-running test instance and re-checked against upstream SearXNG activity through 2026-05-04. It avoids the most common stand-up gotchas: JSON format disabled, Google returning CAPTCHA on aggregator traffic, and Cloudflare-blocked engines wedging the result fan-in. Adjust per your jurisdiction — engines blocked from one network may work fine from another.

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
