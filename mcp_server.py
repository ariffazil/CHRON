"""CHRON MCP Server entry point.

Imports the FastMCP app from server.py and runs it.
This is the ExecStart target for chron-mcp.service.

Port: 18102 (CHRON_MCP_PORT env override)
Transport: streamable-http (matches federation /mcp pattern)

DITEMPA BUKAN DIBERI ⚒️
"""

import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from chron.server import mcp

CHRON_MCP_PORT = int(os.environ.get("CHRON_MCP_PORT", 18102))

if __name__ == "__main__":
    print(f"CHRON MCP starting on port {CHRON_MCP_PORT} (streamable-http)")
    mcp.run(transport="streamable-http", port=CHRON_MCP_PORT, host="127.0.0.1")
