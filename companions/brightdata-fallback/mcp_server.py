#!/usr/bin/env python3
"""
Brightdata Fallback MCP Server.

Minimal MCP server exposing only the `scrape_as_markdown` tool for scraping
URLs that ordinary fetchers fail on (CAPTCHA, paywall, Cloudflare challenge,
403 responses).

Use this as the last hop in a research-workflow URL-reading fallback chain:
  Jina read_url → Ref ref_read_url → Brightdata scrape_as_markdown.

Usage:
    .venv/bin/python mcp_server.py

Environment variables (required):
    BRIGHTDATA_API_TOKEN  — your Brightdata API token (no default)
    BRIGHTDATA_ZONE       — your Web Unlocker zone name (no default)
"""
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

API_TOKEN = os.environ.get("BRIGHTDATA_API_TOKEN")
ZONE = os.environ.get("BRIGHTDATA_ZONE")
API_ENDPOINT = "https://api.brightdata.com/request"

if not API_TOKEN or not ZONE:
    print(
        "ERROR: BRIGHTDATA_API_TOKEN and BRIGHTDATA_ZONE must be set in the environment.",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP("brightdata-fallback")


@mcp.tool()
async def scrape_as_markdown(url: str) -> str:
    """Scrape a blocked URL as markdown using Brightdata Web Unlocker.

    Bypasses CAPTCHA, paywalls, and Cloudflare challenges. Use only when
    other fetchers (Jina, Ref, WebFetch) have failed on the same URL.

    Args:
        url: The URL to scrape.

    Returns:
        Scraped content as markdown, or an error message starting with "Error:".
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            API_ENDPOINT,
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "url": url,
                "zone": ZONE,
                "format": "raw",
                "data_format": "markdown",
            },
        )

        if response.status_code == 200:
            return response.text
        else:
            return f"Error: HTTP {response.status_code} - {response.text[:500]}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
