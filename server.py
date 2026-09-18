"""CHRON MCP Server — FastMCP surface for temporal cortex.

Exposes CHRON's prediction, verification, calibration, and episode
stores as MCP tools. Agents call these on wake-up to gain temporal
awareness without background consciousness.

Port: 18102 (CHRON_MCP_PORT env override)
Transport: streamable-http (federation /mcp pattern)
Health: /health

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure chron package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP

from chron.chron_store import get_store
from chron.chron_prediction import (
    get_active,
    get_due,
    get_verified,
    compute_calibration,
    load_predictions,
    generate_from_chron_events,
)
from chron.chron_verify import (
    run_verification,
    _append_verification_record,
    _is_already_verified,
)
from chron.chron_learn import extract_lessons, load_lessons, get_candidates
from chron.chron_prediction import verify_prediction, create_prediction
from chron.chron_episode import verify_from_result, predict_from_prediction
from chron.chron_attention_debt import get_attention_debt, get_ad_summary

mcp = FastMCP(
    "CHRON",
    instructions=(
        "CHRON — Temporal Consequence Tracker. "
        "Predicts, verifies, calibrates, learns. "
        "Not a scheduler. Not a memory store. "
        "The organ that answers: What did we expect? What happened? Were we wrong?"
    ),
)

MYT = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── TOOLS ─────────────────────────


@mcp.tool()
def chron_predictions_due() -> dict:
    """Get predictions whose verify_at has arrived — the temporal delta.

    Returns predictions that CHRON expects to verify NOW.
    This is the 'what did we expect vs what happened' signal.
    """
    due = get_due()
    return {
        "due": due,
        "count": len(due),
        "query_time": _now_iso(),
        "next_verify_at": _next_verify_at(),
    }


@mcp.tool()
def chron_calibration_state() -> dict:
    """Get calibration statistics — how wrong have we been?

    Returns Brier scores, accuracy, error distribution.
    The institution's epistemic health metric.
    """
    calibration = compute_calibration()
    return {
        "calibration": calibration,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_last_loop() -> dict:
    """Get the last FULL_LOOP entry from loop_log.

    Returns the most recent metabolic cycle result:
    episodes created, predictions verified, lessons extracted.
    """
    loop_log = Path("/root/chron/data/loop_log.jsonl")
    if not loop_log.exists():
        return {"status": "NO_LOOP_LOG", "query_time": _now_iso()}

    last_full_loop = None
    with open(loop_log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("arrow") == "FULL_LOOP":
                    last_full_loop = entry
            except json.JSONDecodeError:
                continue

    if not last_full_loop:
        return {"status": "NO_FULL_LOOP_YET", "query_time": _now_iso()}

    return {
        "last_loop": last_full_loop,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_active_events(audience: str = "all") -> dict:
    """Get chron_events filtered by audience.

    Args:
        audience: Filter —
            'arif'  → events addressed to arif + shared (audience in ['arif','both'])
            'syed'  → events addressed to syed + shared (audience in ['syed','both'])
            'both'  → SHARED-ONLY events (audience == 'both'). NOT a superset.
            'all'   → EVERY event regardless of audience (use this for full inventory).
            Default: 'all' (returns every event).

    Contract note (2026-09-18 witness report fix):
        'both' means "addressed to both principals" — shared-only.
        It does NOT mean "return everything". Use 'all' for full inventory.
    """
    chron_events = Path("/root/AAA/scripts/chron_events.json")
    if not chron_events.exists():
        return {"events": [], "count": 0, "query_time": _now_iso()}

    data = json.loads(chron_events.read_text())
    events = data.get("events", [])

    if audience != "all":
        events = [e for e in events if e.get("audience") in (audience, "both")]

    today = datetime.now(MYT).date()
    enriched = []
    for e in events:
        target = e.get("target_date")
        days_until = None
        if target:
            try:
                days_until = (datetime.strptime(target, "%Y-%m-%d").date() - today).days
            except ValueError:
                pass
        enriched.append({**e, "days_until": days_until})

    return {
        "events": enriched,
        "count": len(enriched),
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_active_predictions(limit: int = 50, source_id: str = "") -> dict:
    """Get all active (unverified) predictions.

    The institution's current expectations about the future.

    Args:
        limit: Max predictions to return (default 50)
        source_id: Filter by source event ID (e.g. 'budget-2027'). Empty = all.
    """
    preds = get_active()
    if source_id:
        preds = [p for p in preds if p.get("source_id") == source_id]
    total = len(preds)
    preds = preds[:limit]
    return {
        "predictions": preds,
        "count": len(preds),
        "total_active": total,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_store_stats() -> dict:
    """Get CHRON store statistics — episode/prediction/lesson counts.

    Prediction census is derived from the JOIN of the immutable birth store
    with the verification ledger, not from the birth record alone. A birth
    record always reads ``status: ACTIVE`` (that is the immutability
    invariant), so counting verdicts from it would always report zero.
    """
    store = get_store()
    preds = load_predictions()
    lessons = load_lessons()

    active = get_active()
    verified = get_verified(limit=10000)
    decisive = [
        v for v in verified if v.get("verdict") in ("VERIFIED_CORRECT", "VERIFIED_INCORRECT")
    ]
    unverifiable = [v for v in verified if v.get("verdict") == "UNVERIFIABLE"]
    orphans = [v for v in verified if v.get("orphan")]
    due = get_due()

    return {
        "episodes": {
            "total": store.count(),
            "by_function": store.functions(),
        },
        "predictions": {
            "total": len(preds),
            "active": len(active),
            "verified": len(decisive),
            "unverifiable": len(unverifiable),
            "orphan_verification_records": len(orphans),
            "due_now": len(due),
        },
        "lessons": {
            "total": len(lessons),
            "candidates": len([l for l in lessons if l.get("status") == "CANDIDATE"]),
        },
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_verified_predictions(limit: int = 50, include_void: bool = False) -> dict:
    """Get the verification ledger — what has actually been checked, and how it went.

    This is the read surface for CHRON's *second* act. ``chron_active_predictions``
    shows what we expect; this shows what reality answered. Each row carries the
    birth snapshot (claim, confidence at birth, assumptions, evidence) joined with
    the observed outcome, verdict, error class, and Brier score — so a wrong
    prediction can be diagnosed against the reasoning that produced it.

    Args:
        limit: Max records to return (default 50)
        include_void: Include retracted (VOID) records (default False)

    Verdicts:
        VERIFIED_CORRECT / VERIFIED_INCORRECT — decisive, entered into calibration
        UNVERIFIABLE — verification was attempted, no evidence found; the
            prediction stays open and is NOT scored
        VOID — retracted; not scored (excluded unless include_void)
    """
    rows = get_verified(limit=limit, include_void=include_void)
    return {
        "verified": rows,
        "count": len(rows),
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_temporal_briefing() -> dict:
    """The full temporal briefing for agent wake-up.

    Combines predictions due, calibration state, last loop,
    and active events into one structured packet.
    This is what gets injected into carry_forward.json.
    """
    return {
        "predictions_due": get_due(),
        "calibration": compute_calibration(),
        "attention_debt": get_ad_summary(),
        "last_loop": _get_last_loop(),
        "active_events": _get_active_events_summary(),
        "next_verify_at": _next_verify_at(),
        "generated_at": _now_iso(),
    }


@mcp.tool()
def chron_generate_predictions() -> dict:
    """Generate predictions from chron_events.json.

    Creates prediction objects for events that don't have one yet.
    """
    new_preds = generate_from_chron_events()
    return {
        "generated": len(new_preds),
        "predictions": new_preds,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_record_verification(
    prediction_id: str,
    observed_outcome: str,
    correct: bool,
) -> dict:
    """Record a verification outcome for a prediction. Closes the loop.

    This is the WRITE surface that completes the Arrow of Time.
    An agent observes reality, compares to the prediction, and records
    whether the prediction was correct or incorrect.

    Args:
        prediction_id: The prediction to verify (e.g. 'pred-b73cba59ab86')
        observed_outcome: What actually happened (free text)
        correct: True if prediction was correct, False if incorrect

    Returns:
        Verification record with Brier score, or error if prediction not found.
    """
    # Find the prediction
    preds = load_predictions()
    pred = next((p for p in preds if p["prediction_id"] == prediction_id), None)
    if not pred:
        return {
            "error": f"Prediction {prediction_id} not found",
            "query_time": _now_iso(),
        }

    # Idempotency: skip if already verified
    if _is_already_verified(prediction_id):
        return {
            "status": "ALREADY_VERIFIED",
            "prediction_id": prediction_id,
            "query_time": _now_iso(),
        }

    # Build verification record (does NOT mutate original prediction)
    verif_record = verify_prediction(pred, observed_outcome, correct)

    # Persist to verification_log.jsonl (APPEND-ONLY)
    _append_verification_record(verif_record)

    # Create verify episode
    store = get_store()
    error = 0.0 if correct else 1.0
    error_class = "NONE" if correct else "ASSUMPTION_ERROR"
    ep = verify_from_result(pred, observed_outcome, error, error_class)
    store.append(ep)

    # Emit arifFlow receipt
    _emit_ariflow_verify(
        prediction_id,
        pred.get("claim", "?"),
        observed_outcome,
        error,
        error_class,
        "VERIFIED_CORRECT" if correct else "VERIFIED_INCORRECT",
    )

    return {
        "status": "VERIFIED_CORRECT" if correct else "VERIFIED_INCORRECT",
        "verification_id": verif_record["verification_id"],
        "prediction_id": prediction_id,
        "claim": pred.get("claim"),
        "confidence_at_birth": pred.get("confidence"),
        "observed_outcome": observed_outcome,
        "brier_score": verif_record["brier_score"],
        "verdict": verif_record["verdict"],
        "query_time": _now_iso(),
    }


def _emit_ariflow_verify(
    pred_id: str,
    claim: str,
    observed: str,
    error: float | None,
    error_class: str,
    status: str,
) -> None:
    """Emit verification receipt to arifFlow."""
    try:
        import urllib.request
        import uuid

        receipt = {
            "receipt_id": str(uuid.uuid4()),
            "created_at": _now_iso(),
            "actor_id": "chron",
            "session_id": f"chron-verify-{pred_id[:12]}",
            "step_type": "Verify",
            "step_number": 0,
            "cost_ns": 0,
            "epistemic_label": "Derivation",
            "floor_verdict": "Pass",
            "cooling_decision": "None",
            "summary": f"CHRON verify: {claim[:60]} → error={error} class={error_class} status={status}",
            "routed_organ": "chron",
            "payload": {
                "prediction_id": pred_id,
                "observed": observed,
                "error": error,
                "error_class": error_class,
            },
        }

        req = urllib.request.Request(
            "http://127.0.0.1:7073/ingest",
            data=json.dumps(receipt).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass


# ───────────────────────── EXISTING TOOLS ─────────────────────────


@mcp.tool()
def chron_attention_debt() -> dict:
    """Attention Debt — measures unresolved contradiction backlog.

    AD(t) = Σ (known_contradiction_age_days × consequence_weight)

    Sources: overdue predictions, unresolved HIGH events, SABAR verdicts.
    The core hypothesis: constraint failure occurs first, attention failure
    delays its detection, trust failure propagates it, price records it.

    This tool measures the "attention failure delays its detection" part.
    """
    return get_attention_debt()


@mcp.tool()
def chron_create_event(
    event_id: str,
    title: str,
    target_date: str,
    kind: str = "OBSERVATION",
    audience: str = "both",
    source: str = "agent",
    note: str = "",
    confidence: str = "TENTATIVE",
    consequence: str = "MEDIUM",
    actionability: str = "WATCH",
) -> dict:
    """Create a new event in chron_events.json.

    Allows agents to add temporal commitments that CHRON will track.
    Use for: deadlines, regulatory windows, market events, personal milestones.

    Args:
        event_id: Unique kebab-case ID (e.g. 'my-004-inflation-oct')
        title: Human-readable event description
        target_date: ISO date YYYY-MM-DD
        kind: Event category (FISCAL, MARKET_EVENT, REGULATORY_WINDOW, PERSONAL_SAFE, MACRO_INDICATOR, OBSERVATION)
        audience: Who sees this ('arif', 'syed', 'both')
        source: Where this comes from (URL, institution, agent)
        note: Additional context
        confidence: CONFIRMED / LIKELY / ANNOUNCED / PREDICTED / TENTATIVE
        consequence: HIGH / MEDIUM / LOW
        actionability: PREPARE / WATCH / MONITOR
    """
    chron_events_path = Path("/root/AAA/scripts/chron_events.json")
    if not chron_events_path.exists():
        return {"error": "chron_events.json not found", "query_time": _now_iso()}

    data = json.loads(chron_events_path.read_text())
    events = data.get("events", [])

    # Check for duplicate ID
    existing_ids = {e.get("id") for e in events}
    if event_id in existing_ids:
        return {
            "error": f"Event '{event_id}' already exists",
            "query_time": _now_iso(),
        }

    # Validate date
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return {
            "error": f"Invalid date format: {target_date} (use YYYY-MM-DD)",
            "query_time": _now_iso(),
        }

    new_event = {
        "id": event_id,
        "title": title,
        "target_date": target_date,
        "timezone": "Asia/Kuala_Lumpur",
        "audience": audience,
        "kind": kind,
        "source": source,
        "confidence": confidence,
        "consequence": consequence,
        "actionability": actionability,
        "note": note,
    }

    events.append(new_event)
    data["events"] = events
    data["_updated"] = datetime.now(MYT).strftime("%Y-%m-%d")
    chron_events_path.write_text(json.dumps(data, indent=2, default=str) + "\n")

    return {
        "created": True,
        "event_id": event_id,
        "title": title,
        "target_date": target_date,
        "total_events": len(events),
        "query_time": _now_iso(),
    }


# ───────────────────────── HELPERS ─────────────────────────


def _next_verify_at() -> str | None:
    """Get the next upcoming verify_at from active predictions."""
    now = datetime.now(timezone.utc)
    upcoming = []
    for p in get_active():
        try:
            vat = datetime.fromisoformat(p["verify_at"].replace("Z", "+00:00"))
            if vat > now:
                upcoming.append(vat)
        except Exception:
            pass
    if upcoming:
        return min(upcoming).isoformat()
    return None


def _get_last_loop() -> dict | None:
    """Get last FULL_LOOP entry."""
    loop_log = Path("/root/chron/data/loop_log.jsonl")
    if not loop_log.exists():
        return None
    last = None
    with open(loop_log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("arrow") == "FULL_LOOP":
                    last = entry
            except json.JSONDecodeError:
                continue
    return last


def _get_active_events_summary() -> list[dict]:
    """Get condensed active events for briefing."""
    chron_events = Path("/root/AAA/scripts/chron_events.json")
    if not chron_events.exists():
        return []
    data = json.loads(chron_events.read_text())
    events = data.get("events", [])
    today = datetime.now(MYT).date()
    summary = []
    for e in events:
        target = e.get("target_date")
        days_until = None
        if target:
            try:
                days_until = (datetime.strptime(target, "%Y-%m-%d").date() - today).days
            except ValueError:
                pass
        summary.append(
            {
                "id": e.get("id"),
                "title": e.get("title"),
                "kind": e.get("kind"),
                "target_date": target,
                "days_until": days_until,
                "confidence": e.get("confidence"),
            }
        )
    return summary


# ───────────────────────── HEALTH ─────────────────────────


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Health endpoint for federation probing."""
    from starlette.responses import JSONResponse

    store = get_store()
    preds = load_predictions()
    calibration = compute_calibration()
    active = get_active()
    ledger = get_verified(limit=10000)
    decisive = [
        v
        for v in ledger
        if v.get("verdict") in ("VERIFIED_CORRECT", "VERIFIED_INCORRECT")
    ]

    ad_summary = get_ad_summary()

    return JSONResponse(
        {
            "status": "ok",
            "organ": "CHRON",
            "port": int(os.environ.get("CHRON_MCP_PORT", 18102)),
            "episodes": store.count(),
            "predictions": {
                "total": len(preds),
                "active": len(active),
                "verified": len(decisive),
                "unverifiable": calibration.get("unverifiable", 0),
                "orphan_verification_records": calibration.get("orphan_records", 0),
            },
            "calibration": {
                "total_verified": calibration.get("total", 0),
                "accuracy": calibration.get("accuracy"),
                "mean_brier": calibration.get("mean_brier"),
            },
            "attention_debt": {
                "total_ad": ad_summary.get("total_ad", 0),
                "item_count": ad_summary.get("item_count", 0),
                "growth_rate_per_day": ad_summary.get("growth_rate_per_day"),
            },
            "query_time": _now_iso(),
        }
    )


# ───────────────────────── MAIN ─────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("CHRON_MCP_PORT", 18102))
    print(f"CHRON MCP starting on port {port} (streamable-http)")
    mcp.run(transport="streamable-http", port=port, host="127.0.0.1")
