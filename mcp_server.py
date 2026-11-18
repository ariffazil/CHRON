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

# 2026-11-04 (333-AGI · Gemini/Connected Apps): declare MCP tool annotations
# (readOnly/destructive/openWorld) so an agentic client can tell a read from a
# write. Without them the MCP default (readOnlyHint=false, destructiveHint=true)
# makes Gemini prompt for confirmation on EVERY call.
# Evidence: RECEIPT_GEMINI_MCP_READINESS_2026-11-04.md
from mcp_annotations import patch_tool_annotations

_patched = patch_tool_annotations(mcp)
print(f"[MCP] annotations patched on {_patched} tools")

CHRON_MCP_PORT = int(os.environ.get("CHRON_MCP_PORT", 18102))

if __name__ == "__main__":
    print(f"CHRON MCP starting on port {CHRON_MCP_PORT} (streamable-http)")
    mcp.run(transport="streamable-http", port=CHRON_MCP_PORT, host="127.0.0.1")
