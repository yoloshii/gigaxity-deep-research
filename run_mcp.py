#!/usr/bin/env python
"""Standalone MCP server runner."""
import sys
import os

# Ensure the project is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import and run
from src.config import settings
from src.mcp_server import mcp

settings.require_llm_key()

# State the effective wall-clock policy once at startup. It is a server-wide
# resource policy and it is DISABLED by default, so an operator who expects a
# ceiling needs to see that there isn't one. stderr, not stdout: stdout is the
# stdio MCP transport.
_cap = settings.llm_wall_clock_cap
print(
    "LLM wall-clock cap: "
    + (f"{_cap}s" if _cap > 0 else "disabled")
    + f" | progress heartbeat: every {settings.progress_heartbeat_interval}s",
    file=sys.stderr,
)

mcp.run(show_banner=False)
