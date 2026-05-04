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
mcp.run(show_banner=False)
