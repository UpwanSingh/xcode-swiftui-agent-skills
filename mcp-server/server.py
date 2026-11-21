#!/usr/bin/env python3
"""Entry point: `python3 mcp-server/server.py` starts the ios-debug MCP server on stdio.

Stdlib only (Python 3.9+); no installation step. See mcp-server/README.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ios_debug_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
