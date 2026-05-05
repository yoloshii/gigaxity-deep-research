# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

Only the latest release on `main` receives security fixes.

## Architecture Context

Gigaxity Deep Research is an **MCP server + REST API** that orchestrates third-party search providers and a hosted LLM. The server runs as a local process and communicates outbound with:

- OpenRouter (default LLM backend) over HTTPS
- A SearXNG instance you point it at (self-hosted or third-party)
- Tavily and LinkUp (optional fallback search providers)

No telemetry. No phone-home. The server holds your API keys in environment variables and forwards them only to the providers you've configured.

## Threat Model

Given the orchestration architecture, the primary security concerns are:

1. **API key exposure** — `RESEARCH_LLM_API_KEY`, `RESEARCH_TAVILY_API_KEY`, and `RESEARCH_LINKUP_API_KEY` are read from environment. Reports of unintended logging, error messages, or response payloads that include these keys should be filed.

2. **Per-request key passthrough** — The REST API accepts an `X-LLM-Api-Key` header that overrides the env-configured key for multi-tenant deployments. Any path where this header leaks across requests, into logs, or into stored responses should be reported.

3. **Prompt injection via search results** — Web content fetched from search providers is fed to the LLM during synthesis. Adversarial content that bypasses prompt-isolation and causes the LLM to leak system instructions, exfiltrate keys, or call unintended tools should be reported.

4. **SSRF / open redirect** — The server accepts URL inputs (e.g. for content extraction). Reports of unvalidated requests that hit internal IPs, cloud-metadata endpoints, or arbitrary intranet hosts are in scope.

5. **Dependency vulnerabilities** — Third-party packages (`fastapi`, `httpx`, `openai`, `tavily-python`, `linkup-sdk`, `fastmcp`) may have their own vulnerabilities.

6. **MCP tool abuse** — The MCP server exposes `discover`, `synthesize`, `reason`, and `ask` tools. If a tool can be coerced into actions outside its intended scope (e.g. arbitrary file read, server-side request forgery), report it.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Use GitHub's [private vulnerability reporting](https://github.com/yoloshii/gigaxity-deep-research/security/advisories/new) and include:

- Description of the vulnerability
- Steps to reproduce
- Impact assessment (what can an attacker do?)
- Suggested fix if you have one

You should receive an acknowledgment within 72 hours. Critical issues fixed within 7 days; lower severity in the next release.

## Security Best Practices for Users

- Keep Python and dependencies up to date (`pip install -U -e ".[dev]"`)
- Store API keys in `.env` — never commit them. The shipped `.gitignore` excludes `.env` already.
- If you expose the REST API beyond `localhost`, put it behind an authenticated reverse proxy. There is no built-in auth.
- Bind the server to `127.0.0.1` (not `0.0.0.0`) when running on a shared machine.
- If you accept per-request LLM keys, ensure your reverse proxy strips the `X-LLM-Api-Key` header from access logs.
