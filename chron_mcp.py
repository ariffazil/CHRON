"""CHRON MCP — query API for other organs.

Exposes CHRON data as MCP tools:
  - chron_episodes: query episodes by function/principal/time
  - chron_predictions: list predictions by status
  - chron_calibration: get calibration stats
  - chron_verify_due: run verification on due predictions
  - chron_lessons: get extracted lessons
  - chron_store_stats: episode/prediction counts

CHRON MCP is the query interface. CHRON is the organ.
If MCP dies, capability lives in the store.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import CHRON modules
from chron.chron_store import get_store
from chron.chron_prediction import (
    get_active,
    get_due,
    get_verified,
    compute_calibration,
    load_predictions,
    generate_from_chron_events,
)
from chron.chron_verify import run_verification
from chron.chron_learn import extract_lessons, load_lessons, get_candidates


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── TOOLS ─────────────────────────


def chron_episodes(
    function: str | None = None,
    principal: str | None = None,
    since: str | None = None,
    limit: int = 10,
) -> dict:
    """Query CHRON episodes.

    Args:
        function: Filter by function (observe/predict/verify/learn)
        principal: Filter by principal (arif/syed/federation)
        since: ISO timestamp — only episodes after this time
        limit: Max results (default 10)
    """
    store = get_store()
    episodes = store.query(
        function=function, principal=principal, since=since, limit=limit
    )
    return {
        "episodes": episodes,
        "total": store.count(),
        "functions": store.functions(),
        "query_time": _now_iso(),
    }


def chron_predictions(status: str = "all") -> dict:
    """List predictions by status.

    Args:
        status: Filter — 'all', 'active', 'due', 'verified', 'expired'
    """
    if status == "active":
        preds = get_active()
    elif status == "due":
        preds = get_due()
    elif status == "verified":
        preds = get_verified()
    else:
        preds = load_predictions()

    return {
        "predictions": preds,
        "count": len(preds),
        "status_filter": status,
        "query_time": _now_iso(),
    }


def chron_calibration() -> dict:
    """Get calibration statistics.

    Returns Brier scores, accuracy, error distribution.
    """
    calibration = compute_calibration()
    return {
        "calibration": calibration,
        "query_time": _now_iso(),
    }


def chron_verify_due(dry_run: bool = False) -> dict:
    """Run verification on all due predictions.

    Args:
        dry_run: If True, don't update predictions or create episodes.
    """
    result = run_verification(dry_run=dry_run)
    return result


def chron_lessons(status: str = "all") -> dict:
    """Get extracted lessons.

    Args:
        status: Filter — 'all', 'candidates', 'promoted'
    """
    if status == "candidates":
        lessons = get_candidates()
    else:
        lessons = load_lessons()

    return {
        "lessons": lessons,
        "count": len(lessons),
        "status_filter": status,
        "query_time": _now_iso(),
    }


def chron_store_stats() -> dict:
    """Get CHRON store statistics."""
    store = get_store()
    preds = load_predictions()
    lessons = load_lessons()

    active = [p for p in preds if p.get("status") == "ACTIVE"]
    verified = [
        p
        for p in preds
        if p.get("status") in ("VERIFIED_CORRECT", "VERIFIED_INCORRECT")
    ]
    due = get_due()

    return {
        "episodes": {
            "total": store.count(),
            "by_function": store.functions(),
        },
        "predictions": {
            "total": len(preds),
            "active": len(active),
            "verified": len(verified),
            "due_now": len(due),
        },
        "lessons": {
            "total": len(lessons),
            "candidates": len([l for l in lessons if l.get("status") == "CANDIDATE"]),
        },
        "query_time": _now_iso(),
    }


def chron_generate_predictions() -> dict:
    """Generate predictions from chron_events.json.

    Returns new predictions created.
    """
    new_preds = generate_from_chron_events()
    return {
        "generated": len(new_preds),
        "predictions": new_preds,
        "query_time": _now_iso(),
    }


# ───────────────────────── TOOL REGISTRY ─────────────────────────

TOOLS = {
    "chron_episodes": {
        "fn": chron_episodes,
        "description": "Query CHRON episodes by function/principal/time",
        "parameters": {
            "function": {
                "type": "string",
                "enum": ["observe", "predict", "verify", "learn"],
                "optional": True,
            },
            "principal": {
                "type": "string",
                "enum": ["arif", "syed", "federation"],
                "optional": True,
            },
            "since": {
                "type": "string",
                "description": "ISO timestamp",
                "optional": True,
            },
            "limit": {"type": "integer", "default": 10},
        },
    },
    "chron_predictions": {
        "fn": chron_predictions,
        "description": "List predictions by status",
        "parameters": {
            "status": {
                "type": "string",
                "enum": ["all", "active", "due", "verified"],
                "default": "all",
            },
        },
    },
    "chron_calibration": {
        "fn": chron_calibration,
        "description": "Get calibration statistics (Brier scores, accuracy)",
        "parameters": {},
    },
    "chron_verify_due": {
        "fn": chron_verify_due,
        "description": "Run verification on due predictions",
        "parameters": {
            "dry_run": {"type": "boolean", "default": False},
        },
    },
    "chron_lessons": {
        "fn": chron_lessons,
        "description": "Get extracted lessons from verified predictions",
        "parameters": {
            "status": {
                "type": "string",
                "enum": ["all", "candidates"],
                "default": "all",
            },
        },
    },
    "chron_store_stats": {
        "fn": chron_store_stats,
        "description": "Get CHRON store statistics",
        "parameters": {},
    },
    "chron_generate_predictions": {
        "fn": chron_generate_predictions,
        "description": "Generate predictions from chron_events.json",
        "parameters": {},
    },
}


def list_tools() -> list[dict]:
    """List all available CHRON MCP tools."""
    return [
        {
            "name": name,
            "description": tool["description"],
            "parameters": tool["parameters"],
        }
        for name, tool in TOOLS.items()
    ]


def call_tool(name: str, **kwargs) -> dict:
    """Call a CHRON MCP tool by name."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}", "available": list(TOOLS.keys())}
    try:
        result = TOOLS[name]["fn"](**kwargs)
        return result
    except Exception as e:
        return {"error": str(e), "tool": name}
