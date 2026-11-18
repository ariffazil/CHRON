"""MCP tool annotations for CHRON — Google/Connected Apps conformance.

WHY THIS EXISTS
---------------
Google's Connected Apps Platform reads MCP tool annotations to decide whether
to prompt the user for confirmation before a call. The MCP spec default when
`annotations` is ABSENT is readOnlyHint=false + destructiveHint=true, so a
server that declares nothing gets a confirmation prompt on EVERY call —
which is exactly what makes an agentic client unusable.

Classification rule (semantic, not syntactic):
  readOnlyHint    = does the tool change the organ's durable state?
  destructiveHint = is the write irreversible / does it destroy data?
  openWorldHint   = does the tool deterministically reach the PUBLIC INTERNET
                    or a HOSTED THIRD PARTY?
                    Federation-internal calls (arifOS :8088, arifFlow :7073,
                    FRAME :18085) are NOT open-world.

Every entry below was derived by reading the tool body, not by guessing.
Evidence lives in RECEIPT_GEMINI_MCP_READINESS_2026-11-04.md.
"""

from __future__ import annotations

from typing import Any

# read-only: 12 · mutating: 12 · destructive: 0 (all writes are append-only
# or rebuildable derived caches — no CHRON tool deletes or overwrites history)
TOOL_ANNOTATIONS: dict[str, dict[str, Any]] = {
    # ── pure reads ────────────────────────────────────────────────────────
    "chron_predictions_due": {
        "title": "Predictions Due",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_calibration_state": {
        "title": "Calibration State",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_last_loop": {
        "title": "Last Loop",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_active_events": {
        "title": "Active Events",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_active_predictions": {
        "title": "Active Predictions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_store_stats": {
        "title": "Store Stats",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_verified_predictions": {
        "title": "Verified Predictions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_timing_audit": {
        "title": "Timing Audit",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_lessons": {
        "title": "Lessons",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_episodes": {
        "title": "Episodes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_proxy_reality_state": {
        "title": "Proxy Reality State",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chron_time_now": {
        "title": "Current Time",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    # ── mutating (append-only / derived cache) ────────────────────────────
    "chron_temporal_briefing": {
        "title": "Temporal Briefing",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_generate_predictions": {
        "title": "Generate Predictions",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_record_verification": {
        "title": "Record Verification",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_amend_verification": {
        "title": "Amend Verification Reason",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_reconcile_state": {
        "title": "Reconcile State",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_attention_debt": {
        "title": "Attention Debt",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_resolve_unverifiable": {
        "title": "Resolve Unverifiable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_resolve_event": {
        "title": "Resolve Event",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_create_event": {
        "title": "Create Event",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_proxy_reality_register": {
        "title": "Register Proxy Pair",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_proxy_reality_observe": {
        "title": "Observe Proxy Pair",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_frame_witness": {
        "title": "Frame Witness",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chron_time_now": {
        "title": "Observe Live Clock",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


def patch_tool_annotations(mcp_server: Any) -> int:
    """Attach annotations to already-registered tools. Returns the count patched.

    Post-registration mutation is used (not per-decorator kwargs) because it is
    the pattern already proven live in WELL's organ, it needs zero edits to the
    23 decorator sites, and it also reaches tools registered by helper modules.
    """
    import asyncio

    from mcp.types import ToolAnnotations

    async def _do() -> int:
        patched = 0
        for name, anno in TOOL_ANNOTATIONS.items():
            try:
                tool = await mcp_server.get_tool(name)
                if tool is not None and hasattr(tool, "annotations"):
                    tool.annotations = ToolAnnotations(**anno)
                    patched += 1
            except Exception:
                continue
        return patched

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_do())
    # A loop is already running (server started in-process): schedule the patch.
    loop.create_task(_do())
    return 0
