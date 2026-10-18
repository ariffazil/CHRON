"""CHRON MCP Server — FastMCP surface for temporal cortex.

Exposes CHRON's prediction, verification, calibration, episode,
lesson, proxy-reality, and attention-debt stores as MCP tools.

21 tools · 8 resources · 4 prompts · v4.1.0 (2026-10-06 — chron_frame_witness bridge lit)

Arrow of Time lifecycle:
    Event → Prediction → Verification → Error → Lesson → Policy
    chron_create_event     chron_record_verification    chron_lessons
    chron_generate_preds   chron_resolve_unverifiable

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

#: Event store used by chron_resolve_event (module constant: tests monkeypatch this).
CHRON_EVENTS_STORE = Path("/root/chron/chron_events.json")

from chron.chron_store import (
    get_store,
    enforce_count_invariant,
    STORE_GENERATION,
    SCHEMA_VERSION,
)
from chron.chron_prediction import (
    get_active,
    get_due,
    get_verified,
    compute_calibration,
    load_predictions,
    generate_from_chron_events,
    resolved_ids,
    _canonical_verdict,
    _DECISIVE,
    VERDICT_UNVERIFIABLE,
    VERDICT_CLOSED_UNTESTABLE,
)
from chron.chron_verify import (
    run_verification,
    _append_verification_record,
    _is_already_verified,
)
from chron.chron_learn import extract_lessons, load_lessons, get_candidates
from chron.chron_prediction import (
    verify_prediction,
    create_prediction,
    resolution_states,
)
from chron.chron_episode import verify_from_result, predict_from_prediction
from chron.chron_attention_debt import get_attention_debt, get_ad_summary
from chron.chron_proxy_reality import (
    register_pair,
    list_pairs,
    observe as pr_observe,
    compute_correlation,
    compute_all as pr_compute_all,
    seed_defaults as pr_seed_defaults,
)
from chron.chron_frame import (
    request_witness as fw_request,
    check_witness_results as fw_results,
    record_witness_result as fw_record,
    FRAME_URL,
)
import urllib.request
import urllib.error
from enum import Enum


class AudienceFilter(str, Enum):
    arif = "arif"
    syed = "syed"
    both = "both"
    all = "all"


class MetricType(str, Enum):
    proxy = "proxy"
    reality = "reality"


class ConfidenceLevel(str, Enum):
    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    ANNOUNCED = "ANNOUNCED"
    PREDICTED = "PREDICTED"
    TENTATIVE = "TENTATIVE"


class ConsequenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ActionabilityLevel(str, Enum):
    PREPARE = "PREPARE"
    WATCH = "WATCH"
    MONITOR = "MONITOR"


class EventKind(str, Enum):
    FISCAL = "FISCAL"
    MARKET_EVENT = "MARKET_EVENT"
    REGULATORY_WINDOW = "REGULATORY_WINDOW"
    PERSONAL_SAFE = "PERSONAL_SAFE"
    MACRO_INDICATOR = "MACRO_INDICATOR"
    MONETARY_POLICY = "MONETARY_POLICY"
    OBSERVATION = "OBSERVATION"


mcp = FastMCP(
    "CHRON",
    version="4.1.0",
    instructions=(
        "CHRON — Temporal Consequence Tracker. v4.1.0. "
        "21 tools across the Arrow of Time: Event → Prediction → Verification → "
        "Error → Lesson. Predicts, verifies, calibrates, learns. "
        "chron_frame_witness bridges the independent FRAME witness (separation of "
        "powers: FRAME observes from outside, arifOS adjudicates). "
        "Not a scheduler. Not a memory store. "
        "The organ that answers: What did we expect? What happened? Were we wrong? "
        "What did we learn? Are our proxies gaming us?"
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
    Call this BEFORE chron_record_verification to find what needs closing.

    The Arrow of Time: active → due → verified → calibrated → lesson.
    This tool sits at the active→due boundary. Every prediction returned
    here represents an unpaid epistemic debt.
    """
    due = get_due()

    # Unbound predictions never enter get_due() (status is UNBOUND, not ACTIVE),
    # so without this counter they age silently forever: no verifier sees them,
    # no debt is minted, and the arrow never closes. Expose them where agents
    # look for temporal work, not only in the offline scanner.
    now = datetime.now(timezone.utc)
    resolved = resolved_ids()
    stale_unbound = []
    for p in load_predictions():
        if p.get("status") != "UNBOUND" or p.get("prediction_id") in resolved:
            continue
        try:
            verify_at = datetime.fromisoformat(
                str(p.get("verify_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if now >= verify_at:
            stale_unbound.append(
                {
                    "prediction_id": p.get("prediction_id"),
                    "claim": (p.get("claim") or "")[:160],
                    "verify_at": p.get("verify_at"),
                    "source_id": p.get("source_id"),
                }
            )

    return {
        "due": due,
        "count": len(due),
        "stale_unbound": stale_unbound,
        "stale_unbound_count": len(stale_unbound),
        "query_time": _now_iso(),
        "next_verify_at": _next_verify_at(),
    }


@mcp.tool()
def chron_calibration_state() -> dict:
    """Get calibration statistics — how wrong have we been?

    Returns Brier scores, accuracy, error distribution, and per-error-class
    breakdown. The institution's epistemic health metric.

    A Brier score of 0.25 = coin-flip calibration. Below 0.15 = useful.
    Accuracy below 60% at high confidence (>0.8) = systematic overconfidence.
    This is the evidence that drives the Arrow of Time: verify → calibrate.
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

    If this shows 0 verifications and 0 lessons across multiple loops,
    CHRON is in observation-only mode — the prediction→verification→lesson
    pipeline is broken. This is the canary for metabolic health.
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
def chron_active_events(audience: AudienceFilter = AudienceFilter.all) -> dict:
    """Get chron_events filtered by audience — the institution's known temporal anchors.

    Returns events with computed days_until for each target_date.
    Events are the source material from which predictions are generated.
    Call chron_generate_predictions after adding new events.

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

    Phase 1 fix: invalid audience values now FAIL-CLOSED via Pydantic enum
    validation. Previously 'GARBAGE' silently returned the 'both' subset —
    a privacy bug. Now such requests are rejected at the protocol boundary.
    """
    chron_events = Path("/root/chron/chron_events.json")
    if not chron_events.exists():
        return {"events": [], "count": 0, "query_time": _now_iso()}

    data = json.loads(chron_events.read_text())
    events = data.get("events", [])

    aud = audience.value
    if aud != "all":
        events = [e for e in events if e.get("audience") in (aud, "both")]

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
        "audience_filter": aud,
        "query_time": _now_iso(),
    }


def _resolve_prediction_audience(pred: dict) -> str:
    """Resolve the effective audience of one prediction.

    receipt rcpt-2026-09-19-chron-audience-leak-fix
    evidence /root/chron/data/predictions.jsonl  (live store, read-only)
    evidence /root/chron/chron_events.json (event store, read-only)

    Resolution order:
      1. explicit `audience` on the prediction row
      2. the audience of the source event named by `source_id`
      3. UNRESOLVED — and an unresolved row is never shared.
    """
    explicit = pred.get("audience")
    if explicit in ("arif", "syed", "both"):
        return explicit
    sid = pred.get("source_id")
    if sid:
        try:
            data = json.loads(Path("/root/chron/chron_events.json").read_text())
            for e in data.get("events", []):
                if e.get("id") == sid and e.get("audience") in ("arif", "syed", "both"):
                    return e["audience"]
        except (OSError, json.JSONDecodeError):
            pass
    return "unresolved"


@mcp.tool()
def chron_active_predictions(
    limit: int = 50,
    source_id: str = "",
    audience: AudienceFilter = AudienceFilter.all,
) -> dict:
    """Get all active (unverified) predictions — the institution's current
    expectations about the future that haven't been tested yet.

    Every active prediction is a promise. CHRON will hold it until
    its verify_at arrives, then it becomes due. Callers should cross-
    reference with chron_predictions_due() to see what's overdue.

    Args:
        limit: Max predictions to return (default 50)
        source_id: Filter by source event ID (e.g. 'budget-2027'). Empty = all.
        audience: Privacy filter — same contract as chron_active_events:
            'arif'  → predictions addressed to arif + shared
            'syed'  → predictions addressed to syed + shared
            'both'  → SHARED-ONLY. Correct for two-person surfaces.
            'all'   → EVERY prediction (full inventory).
            Default: 'all'.

    PRIVACY LAW (F13, 2026-09-19):
        Unresolved-audience rows are EXCLUDED (fail-safe), never included
        by default. The count excluded is returned in audience_unresolved.
    """
    aud = audience.value
    preds = get_active()
    if source_id:
        preds = [p for p in preds if p.get("source_id") == source_id]

    unresolved: list = []
    if aud != "all":
        resolved = []
        for p in preds:
            eff = _resolve_prediction_audience(p)
            if eff == "unresolved":
                unresolved.append(p.get("prediction_id"))
                continue
            if aud == "both":
                if eff == "both":
                    resolved.append({**p, "effective_audience": eff})
            elif eff in (aud, "both"):
                resolved.append({**p, "effective_audience": eff})
        preds = resolved

    total = len(preds)
    preds = preds[:limit]
    return {
        "audience": aud,
        "audience_unresolved": unresolved,
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
        v
        for v in verified
        if v.get("verdict") in ("VERIFIED_CORRECT", "VERIFIED_INCORRECT")
    ]
    unverifiable = [v for v in verified if v.get("verdict") == "UNVERIFIABLE"]
    orphans = [v for v in verified if v.get("orphan")]
    due = get_due()
    rstates = resolution_states()

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
            # Resolution-process states (audit 2026-09-25 #2) — derived, never
            # mutated: OPEN / DUE / AWAITING_EVIDENCE / UNRESOLVABLE. The
            # complement of these ids is the RESOLVED set.
            "resolution_states": {k: len(v) for k, v in rstates.items()},
            "resolution_states_detail": rstates,
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

    Combines predictions due, calibration state, attention debt,
    last loop, and active events into one structured packet.
    This is what gets injected into carry_forward.json at session init.

    Call this FIRST when an agent wakes up. It answers:
    - What did we expect? (active predictions)
    - What's overdue? (predictions due)
    - How wrong have we been? (calibration)
    - What's the attention backlog? (attention debt)
    - What did the last metabolic cycle do? (last loop)
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
    """Record a verification outcome for a prediction. CLOSES THE ARROW.

    This is the WRITE surface that completes the Arrow of Time:
    active → due → VERIFIED → calibrated → lesson.

    An agent observes reality, compares to the prediction, and records
    whether the prediction was correct or incorrect. CHRON then computes
    the Brier score and writes a permanent verification record.

    After calling this, call chron_calibration_state() to see updated
    calibration, and chron_lessons() to check for extractable lessons.

    Args:
        prediction_id: The prediction to verify (e.g. 'pred-b73cba59ab86')
        observed_outcome: What actually happened (free text — becomes evidence)
        correct: True if prediction was correct, False if incorrect

    Returns:
        Verification record with Brier score, verdict, and verification_id.
        If the prediction has already been verified, returns ALREADY_VERIFIED.
        If the prediction is not found, returns _is_error: true.
    """
    # Find the prediction
    preds = load_predictions()
    pred = next((p for p in preds if p["prediction_id"] == prediction_id), None)
    if not pred:
        # Phase 1: explicit error marker. Some clients also check isError
        # at the protocol layer, but this ensures the body itself declares
        # the failure regardless of which field the client inspects.
        return {
            "_is_error": True,
            "error_code": "PREDICTION_NOT_FOUND",
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
def chron_reconcile_state(force: bool = False) -> dict:
    """
    Phase 0 — reconcile in-memory state with JSONL truth.

    Returns the invariant status:
      - jsonl_count: actual episode lines on disk
      - index_count: in-memory index size
      - drift: index - jsonl (must be ≤ 0 unless signed compaction exists)
      - invariant_held: bool
      - generation, schema_version, compaction_receipt

    If force=True, rebuilds the in-memory index from JSONL.
    This is the tool an operator runs after a state contradiction
    to surface — not to silently mask — the gap.
    """
    inv = enforce_count_invariant()

    rebuilt = False
    if force or not inv["invariant_held"]:
        store = get_store()
        store._rebuild_index()
        rebuilt = True
        inv = enforce_count_invariant()

    return {
        "status": "RECONCILED" if rebuilt else "CHECKED",
        "invariant": inv,
        "query_time": _now_iso(),
    }


@mcp.resource("chron://state")
def chron_state_resource() -> str:
    """
    Current generation + invariant status. Read-once snapshot.

    Honest answer to: "What state is CHRON actually in?"
    """
    inv = enforce_count_invariant()
    store = get_store()
    return json.dumps(
        {
            "generation": STORE_GENERATION,
            "schema_version": SCHEMA_VERSION,
            "compaction_receipt": inv["compaction_receipt"],
            "episodes_jsonl": inv["jsonl_count"],
            "episodes_index": inv["index_count"],
            "drift_index_minus_jsonl": inv["drift"],
            "invariant_held": inv["invariant_held"],
            "functions_jsonl": _functions_from_jsonl_safe(),
            "checked_at": inv["checked_at"],
            "note": "Reads (count, functions) ALWAYS serve JSONL fresh. Index is write-time cache only.",
        },
        indent=2,
        default=str,
    )


def _functions_from_jsonl_safe() -> dict[str, int]:
    """Lazy import to avoid circular issues at module top."""
    try:
        from chron.chron_store import _functions_from_jsonl

        return _functions_from_jsonl()
    except Exception:
        return {}


# ───────────────────────── RESOURCES (Phase 3 catalog) ─────────────────────────


@mcp.resource("chron://index")
def chron_index_resource() -> str:
    """
    Capability index — what CHRON can do. Read-once discovery surface.
    Updated 2026-09-28: 19 tools, 8 resources, 4 prompts.
    """
    return json.dumps(
        {
            "organ": "CHRON",
            "version": "4.0.6",
            "protocol_era": "2025-06-18",
            "updated": "2026-09-28",
            "doctrine": (
                "Temporal consequence tracker. Predicts, verifies, calibrates, learns. "
                "Not a scheduler. Not a memory store. "
                "The organ that answers: What did we expect? What happened? Were we wrong?"
            ),
            "tools": [
                "chron_active_events",
                "chron_active_predictions",
                "chron_attention_debt",
                "chron_calibration_state",
                "chron_create_event",
                "chron_episodes",
                "chron_generate_predictions",
                "chron_last_loop",
                "chron_lessons",
                "chron_predictions_due",
                "chron_proxy_reality_observe",
                "chron_proxy_reality_register",
                "chron_proxy_reality_state",
                "chron_reconcile_state",
                "chron_record_verification",
                "chron_resolve_unverifiable",
                "chron_resolve_event",
                "chron_store_stats",
                "chron_temporal_briefing",
                "chron_verified_predictions",
            ],
            "resources": [
                "chron://index",
                "chron://state",
                "chron://predictions/active",
                "chron://events/upcoming",
                "chron://calibration",
                "chron://lessons",
                "chron://schema",
                "chron://doctrine",
            ],
            "prompts": [
                "/briefing",
                "/forecast",
                "/calibrate",
                "/audit",
            ],
            "event_store_path": "/root/chron/chron_events.json",
            "generation": STORE_GENERATION,
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://predictions/active")
def chron_predictions_active_resource() -> str:
    """
    Currently-active (unverified) predictions — the institution's expectations.
    """
    preds = get_active()
    return json.dumps(
        {
            "count": len(preds),
            "horizon": "active",
            "predictions": preds[:50],
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://events/upcoming")
def chron_events_upcoming_resource() -> str:
    """
    Upcoming chron_events with target_date — the institution's known future.
    """
    chron_events = Path("/root/chron/chron_events.json")
    if not chron_events.exists():
        return json.dumps({"events": [], "count": 0, "queried_at": _now_iso()})
    data = json.loads(chron_events.read_text())
    events = data.get("events", [])
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
    enriched.sort(key=lambda x: x.get("days_until") or 99999)
    return json.dumps(
        {
            "events": enriched,
            "count": len(enriched),
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://calibration")
def chron_calibration_resource() -> str:
    """
    Calibration state — how wrong have we been? Brier scores, accuracy, error class.
    """
    return json.dumps(
        {
            "calibration": compute_calibration(),
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://lessons")
def chron_lessons_resource() -> str:
    """
    Extracted lessons — what the institution has learned from verified outcomes.
    """
    lessons = load_lessons()
    return json.dumps(
        {
            "total": len(lessons),
            "candidates": len([l for l in lessons if l.get("status") == "CANDIDATE"]),
            "lessons": lessons,
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://schema")
def chron_schema_resource() -> str:
    """
    Canonical schema reference — what shapes CHRON exchanges with the world.
    """
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "store_generation": STORE_GENERATION,
            "prediction": {
                "prediction_id": "str",
                "claim": "str",
                "confidence": "float [0,1]",
                "expected_outcome": "any",
                "verify_at": "ISO-8601",
                "falsifier": "str",
                "verifier_method": "str",
                "horizon": "str",
                "audience": "arif|syed|both",
                "source": "str",
                "source_id": "str",
                "status": "ACTIVE|VERIFIED_CORRECT|VERIFIED_INCORRECT|UNVERIFIABLE|VOID",
                "created_at": "ISO-8601",
            },
            "episode": {
                "episode_id": "chron-ep-{date}-{function}-{hash8}",
                "function": "observe|verify|learn|predict",
                "valid_time": "ISO-8601",
                "known_at": "ISO-8601",
                "principal": "arif|syed|both",
                "audience": "shared|private|...",
                "generation": STORE_GENERATION,
                "schema_version": SCHEMA_VERSION,
            },
            "temporal_event": {
                "event_id": "kebab-case",
                "title": "str",
                "target_date": "YYYY-MM-DD",
                "kind": "FISCAL|MARKET_EVENT|REGULATORY_WINDOW|PERSONAL_SAFE|MACRO_INDICATOR|MONETARY_POLICY|OBSERVATION",
                "audience": "arif|syed|both",
                "source": "str",
                "confidence": "CONFIRMED|LIKELY|ANNOUNCED|PREDICTED|TENTATIVE",
                "consequence": "HIGH|MEDIUM|LOW",
                "actionability": "PREPARE|WATCH|MONITOR",
            },
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.resource("chron://doctrine")
def chron_doctrine_resource() -> str:
    """
    CHRON's canonical doctrine. What this organ is and is not.
    """
    return json.dumps(
        {
            "name": "CHRON — Temporal Consequence Tracker",
            "purpose": (
                "The time-axis for recursive improvement. Predicts, verifies, "
                "calibrates, learns. Not the brain. Not the judge. Not the executor. "
                "Not long-term memory."
            ),
            "canonical_question": (
                "We changed X because we expected Y. At time T, did Y actually happen — "
                "and what should the institution learn from that?"
            ),
            "owns": "Temporal causality. The BEFORE/AFTER join.",
            "does_not_own": [
                "Memory (what to retain)",
                "Judgment (whether lesson becomes law)",
                "Execution (controlled phenotype)",
                "Witness (independent verification)",
                "Cost (improvement economics)",
                "Meaning (semantic boundary)",
            ],
            "invariants": [
                "No institutional improvement claim without temporal consequence evidence",
                "Predictions become immutable after intervention begins",
                "Every consequential action creates a future verification obligation",
                "Reads serve JSONL truth; index is write-time cache",
                "Audience filter must fail-closed on invalid input",
            ],
            "ratified_by": "F13 sovereign directive, 2026-09-21",
            "queried_at": _now_iso(),
        },
        indent=2,
        default=str,
    )


@mcp.tool()
def chron_attention_debt() -> dict:
    """Attention Debt — measures unresolved contradiction backlog.

    AD(t) = Σ (known_contradiction_age_days × consequence_weight)

    Sources: overdue predictions, unresolved HIGH events, SABAR verdicts.
    The core hypothesis: constraint failure occurs first, attention failure
    delays its detection, trust failure propagates it, price records it.

    This tool measures the "attention failure delays its detection" part.
    AD > 50 = material backlog. AD growth rate > 2/day = attention system
    is falling behind. Call chron_predictions_due() to identify the specific
    items contributing to debt.
    """
    return get_attention_debt()


@mcp.tool()
def chron_lessons(limit: int = 20, status: str = "all") -> dict:
    """Get extracted lessons — what the institution has learned from verified outcomes.

    Lessons are the final stage of the Arrow of Time:
    Event → Prediction → Verification → Error → LESSON.

    A lesson is only useful if it changes future behavior. Lessons with
    status=CANDIDATE are awaiting human ratification. Lessons with
    status=ACTIVE have been ratified and should constrain future predictions.

    The calibration gap (accuracy vs confidence) should shrink as lessons
    accumulate. If it doesn't, the lessons aren't being metabolized.

    Args:
        limit: Max lessons to return (default 20)
        status: Filter — 'all', 'active', 'candidate', 'archived'
    """
    lessons = load_lessons()
    if status != "all":
        lessons = [l for l in lessons if l.get("status") == status.upper()]
    return {
        "lessons": lessons[:limit],
        "count": len(lessons[:limit]),
        "total": len(load_lessons()),
        "candidates": len(
            [l for l in load_lessons() if l.get("status") == "CANDIDATE"]
        ),
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_episodes(
    function: str = "",
    principal: str = "",
    since: str = "",
    limit: int = 50,
) -> dict:
    """Query CHRON episodes — the raw temporal ledger.

    Episodes are the atomic temporal facts: observations, predictions,
    verifications, and lesson extractions. Each episode has a function
    (observe/predict/verify/learn), a principal (arif/syed/both), and
    a valid_time.

    Use this for forensic queries: "what did we know and when?"
    The episode store is append-only JSONL — immutable once written.

    Args:
        function: Filter by function — 'observe', 'predict', 'verify', 'learn'
        principal: Filter by principal — 'arif', 'syed', 'both'
        since: ISO timestamp — only episodes after this time
        limit: Max episodes to return (default 50)
    """
    store = get_store()
    episodes = store.query(
        function=function if function else None,
        principal=principal if principal else None,
        since=since if since else None,
        limit=limit,
    )
    return {
        "episodes": episodes,
        "count": len(episodes),
        "total_store": store.count(),
        "functions": store.functions(),
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_resolve_unverifiable(
    prediction_id: str,
    reason: str = "insufficient_evidence",
) -> dict:
    """Resolve a prediction as UNVERIFIABLE — close it without scoring.

    Some predictions cannot be verified: the evidence never arrived,
    the question became meaningless, or the measurement window closed.
    UNVERIFIABLE predictions are removed from the active set (stopping
    attention debt accumulation) but NOT scored in calibration.

    This is distinct from VERIFIED_INCORRECT: the prediction wasn't wrong,
    it was untestable.

    Args:
        prediction_id: The prediction to resolve
        reason: Why it cannot be verified (free text)
    """
    preds = load_predictions()
    pred = next((p for p in preds if p["prediction_id"] == prediction_id), None)
    if not pred:
        return {
            "_is_error": True,
            "error_code": "PREDICTION_NOT_FOUND",
            "error": f"Prediction {prediction_id} not found",
            "query_time": _now_iso(),
        }

    if _is_already_verified(prediction_id):
        return {
            "status": "ALREADY_VERIFIED",
            "prediction_id": prediction_id,
            "query_time": _now_iso(),
        }

    verif_record = {
        "verification_id": f"vrf-{prediction_id}-UNVERIFIABLE",
        "prediction_id": prediction_id,
        "verdict": "UNVERIFIABLE",
        # Distinct from a verifier's "no evidence yet": this is a decision that the
        # measurement window is shut. Without this class the closure read identically
        # to lateness, so the arrow never left the active set.
        "resolution_class": "CLOSED_UNTESTABLE",
        "brier_score": None,
        "observed_outcome": f"UNVERIFIABLE: {reason}",
        "verified_at": _now_iso(),
        "verified_by": "chron_resolve_unverifiable",
    }
    _append_verification_record(verif_record)

    store = get_store()
    ep = verify_from_result(pred, f"UNVERIFIABLE: {reason}", None, "UNVERIFIABLE")
    store.append(ep)

    # OBSERVE the outcome instead of asserting it (APEX: Outcome > intention).
    still_active = any(p.get("prediction_id") == prediction_id for p in get_active())
    return {
        "status": "UNVERIFIABLE",
        "resolution_class": "CLOSED_UNTESTABLE",
        "arrow": "CLOSED_UNSCORED",
        "still_active": still_active,
        "prediction_id": prediction_id,
        "reason": reason,
        "claim": pred.get("claim"),
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_resolve_event(
    event_id: str,
    observed_outcome: str,
    evidence_ref: str,
) -> dict:
    """Resolve a chron EVENT (not a prediction) with mandatory evidence.

    Closes the event-closure gap (REALITY-CLOSURE-PROTOCOL v0.1, F13-ratified
    2026-10-07): preregistration anchors and sovereign commitments mint no
    predictions, so prediction-arrow discharge can never reach them. A resolved
    event exits attention-debt minting but stays in the ledger (no delete).

    Args:
        event_id: Event id in chron_events.json.
        observed_outcome: What actually happened (free text).
        evidence_ref: File path, sha256, or receipt id — REQUIRED (F2).
    """
    if not (evidence_ref or "").strip():
        return {
            "_is_error": True,
            "error_code": "EVIDENCE_REQUIRED",
            "error": "chron_resolve_event requires evidence_ref (path, sha256, or receipt id)",
            "query_time": _now_iso(),
        }
    events_path = CHRON_EVENTS_STORE
    if not events_path.exists():
        return {
            "_is_error": True,
            "error_code": "EVENTS_FILE_MISSING",
            "error": f"{events_path} not found",
            "query_time": _now_iso(),
        }
    data = json.loads(events_path.read_text())
    events = data.get("events", [])
    evt = next((e for e in events if e.get("id") == event_id), None)
    if evt is None:
        return {
            "_is_error": True,
            "error_code": "EVENT_NOT_FOUND",
            "error": f"Event {event_id} not found",
            "query_time": _now_iso(),
        }
    if evt.get("resolution"):
        return {
            "status": "ALREADY_RESOLVED",
            "event_id": event_id,
            "resolution": evt["resolution"],
            "query_time": _now_iso(),
        }
    evt["resolution"] = {
        "outcome": observed_outcome,
        "evidence_ref": evidence_ref.strip(),
        "resolved_at": _now_iso(),
        "resolved_by": "chron_resolve_event",
    }
    tmp = events_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(events_path)

    # Close persisted OPEN debt items minted from this event. Append-only:
    # rows flip OPEN -> RESOLVED with a resolution note; history is retained.
    debt_closed, debt_error = [], None
    try:
        from chron.chron_attention_debt import _load_all_debt, _resolve_debt

        for it in _load_all_debt():
            if (
                it.get("source") == "unresolved_event"
                and it.get("ref_id") == event_id
                and it.get("status") == "OPEN"
            ):
                if _resolve_debt(
                    it.get("debt_id"),
                    f"event resolved: {observed_outcome[:120]} | evidence: {evidence_ref[:120]}",
                ):
                    debt_closed.append(it.get("debt_id"))
    except Exception as exc:  # event resolution already persisted — report, never hide
        debt_error = f"{type(exc).__name__}: {exc}"

    return {
        "status": "RESOLVED",
        "event_id": event_id,
        "resolution": evt["resolution"],
        "debt_items_closed": debt_closed,
        "debt_ledger_error": debt_error,
        "note": "exits attention-debt minting; ledger retained (append-only discipline)",
        "query_time": _now_iso(),
    }


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
    amplitude: float | None = None,
) -> dict:
    """Create a new event in chron_events.json — plant a temporal stake.

    Events are the raw material. After creating an event, call
    chron_generate_predictions() to create prediction objects from it.
    The chain: Event → Prediction → Verification → Lesson.

    Args:
        event_id: Unique kebab-case ID (e.g. 'my-004-inflation-oct')
        title: Human-readable event description
        target_date: ISO date YYYY-MM-DD
        kind: FISCAL, MARKET_EVENT, REGULATORY_WINDOW, PERSONAL_SAFE, MACRO_INDICATOR, MONETARY_POLICY, OBSERVATION
        audience: 'arif', 'syed', or 'both'
        source: Where this comes from (URL, institution, agent)
        note: Additional context — why this event matters
        confidence: CONFIRMED / LIKELY / ANNOUNCED / PREDICTED / TENTATIVE
        consequence: HIGH / MEDIUM / LOW
        actionability: PREPARE / WATCH / MONITOR
        amplitude: Optional numeric probability in [0.0, 1.0] for a forecast.
            When supplied it becomes the generated prediction's
            confidence_proposed verbatim, OVERRIDING the coarse `confidence`
            enum. This is what lets a superposition of competing branches keep
            its discrimination through recalibration and Brier scoring — without
            it every forecast collapses onto the enum default and the calibrator
            cannot tell a 0.25 claim from a 0.55 claim. Omit for non-forecast
            events (birthdays, deadlines, fiscal windows).
    """
    chron_events_path = Path("/root/chron/chron_events.json")
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

    # Amplitude is optional and validated at the boundary: an out-of-range or
    # non-numeric value is a caller bug, and silently clamping it would forge a
    # probability the forecaster never asserted.
    if amplitude is not None:
        try:
            amp = float(amplitude)
        except (TypeError, ValueError):
            return {
                "error": f"amplitude must be numeric, got {amplitude!r}",
                "query_time": _now_iso(),
            }
        if not 0.0 <= amp <= 1.0:
            return {
                "error": f"amplitude must be within [0.0, 1.0], got {amp}",
                "query_time": _now_iso(),
            }
        new_event["amplitude"] = amp

    events.append(new_event)
    data["events"] = events
    data["_updated"] = datetime.now(MYT).strftime("%Y-%m-%d")
    chron_events_path.write_text(json.dumps(data, indent=2, default=str) + "\n")

    return {
        "created": True,
        "event_id": event_id,
        "title": title,
        "target_date": target_date,
        "amplitude": new_event.get("amplitude"),
        "total_events": len(events),
        "query_time": _now_iso(),
    }


# ───────────────────────── PROXY-REALITY CORRELATION ─────────────────────────


@mcp.tool()
def chron_proxy_reality_register(
    organ: str,
    proxy_name: str,
    proxy_description: str,
    reality_name: str,
    reality_description: str,
    divergence_threshold: float = 0.3,
    diagnostic_question: str = "",
) -> dict:
    """Register a proxy→reality pair for correlation tracking.

    Each organ declares what proxy it measures and what reality it should
    defend. CHRON tracks observations over time and flags divergence:
    proxy improving while reality degrades = GAMING.

    This is the Proxy-Reality Paradox defense: every metric can be gamed.
    The only defense is to measure both the proxy AND the reality it claims
    to represent. Without observations, this is empty instrumentation.

    After registering, call chron_proxy_reality_observe() to feed data.
    14 pairs are pre-seeded. 0 currently have observations.

    Args:
        organ: organ name (e.g. 'GEOX', 'WEALTH', '333-AGI')
        proxy_name: short metric name (e.g. 'test_pass_rate')
        proxy_description: what the proxy measures
        reality_name: short reality name (e.g. 'intended_outcome')
        reality_description: what reality it should represent
        divergence_threshold: min delta to flag divergence (0.0-1.0)
        diagnostic_question: the 'what must this organ ask itself?' question
    """
    pair = register_pair(
        organ=organ,
        proxy_name=proxy_name,
        proxy_description=proxy_description,
        reality_name=reality_name,
        reality_description=reality_description,
        divergence_threshold=divergence_threshold,
        diagnostic_question=diagnostic_question,
    )
    return {
        "registered": True,
        "pair": pair,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_proxy_reality_observe(
    pair_id: str,
    metric_type: str,
    value: float,
    unit: str = "",
    source: str = "",
    evidence_ref: str = "",
) -> dict:
    """Record a proxy or reality observation for a registered pair.

    Call this when you have a measured value for either the proxy metric
    or the reality metric. CHRON computes correlation at query time.

    Args:
        pair_id: the proxy-reality pair ID (from chron_proxy_reality_register or list)
        metric_type: 'proxy' or 'reality'
        value: observed value (normalize to 0.0-1.0 where possible)
        unit: unit of measurement (display only)
        source: what tool/system produced this value
        evidence_ref: path or URL to supporting evidence

    Returns:
        The observation record.
    """
    try:
        obs = pr_observe(
            pair_id=pair_id,
            metric_type=metric_type,
            value=value,
            unit=unit,
            source=source,
            evidence_ref=evidence_ref,
        )
        return {
            "recorded": True,
            "observation": obs,
            "query_time": _now_iso(),
        }
    except ValueError as e:
        return {
            "error": str(e),
            "query_time": _now_iso(),
        }


@mcp.tool()
def chron_proxy_reality_state(organ: str = "", window: int = 5) -> dict:
    """Get proxy-reality correlation state — the Proxy-Reality Paradox metric.

    For each registered pair, computes whether proxy trends match reality
    trends. Flags divergence: proxy improving while reality degrades = GAMING.

    Divergence states:
        HEALTHY     — proxy ↑, reality ↑ (or both stable)
        GAMING      — proxy ↑, reality ↓ (specification gaming / Goodhart)
        WRONG_PROXY — proxy ↓, reality ↑ (measuring the wrong thing)
        FAILING     — proxy ↓, reality ↓ (systemic degradation)
        UNKNOWN     — insufficient data (no observations yet)

    Currently 14 pairs registered, 0 with observations. Until agents call
    chron_proxy_reality_observe(), ALL pairs will return UNKNOWN.

    Args:
        organ: filter by organ name (empty = all organs)
        window: number of recent observations for trend computation (default 5)
    """
    # Auto-seed defaults on first call
    pr_seed_defaults()

    result = pr_compute_all(organ=organ if organ else None, window=window)
    return {
        **result,
        "query_time": _now_iso(),
    }


@mcp.tool()
def chron_frame_witness(
    action: str = "witness",
    claims: list | None = None,
    claim_id: str = "",
) -> dict:
    """Independent witness bridge — have FRAME (outside CHRON) mechanically check atomic claims.

    Separation of powers (constitution): CHRON owns the temporal ledger; FRAME is the
    independent witness and can say CHRON is wrong. FRAME observes, never executes,
    and returns evidence only — VERIFIED / CONTRADICTED / UNVERIFIABLE / STALE / EACCES.
    It NEVER returns SEAL/HOLD; adjudication stays with arifOS.

    Use before recording a verification, when a claim's evidence should be checked by
    a process that did not author it, or whenever CHRON state must be witnessed from
    outside its own walls.

    Args:
        action: 'witness' — submit claims to FRAME's mechanical checker;
                'results' — read recorded witness results (optionally filtered).
        claims: list of {text, evidence:{check, path, field?, equals?, max_age_seconds?}}.
                Evidence kinds: file_exists, file_fresh, json_field. Unpointed prose
                is UNVERIFIABLE by design — fluency never certifies.
        claim_id: optional filter for action='results'.

    Returns:
        For 'witness': FRAME's per-claim rows + rolled-up witness verdict
        (CONFIRMED / CONTESTED / UNVERIFIABLE), evidence quality, contradictions,
        confidence, and audit trail refs (frame_requests.jsonl / frame_results.jsonl).
        For 'results': the recorded witness results.
    """
    if action == "results":
        rows = fw_results(claim_id or None)
        return {
            "witness_results": rows,
            "count": len(rows),
            "query_time": _now_iso(),
        }

    if not isinstance(claims, list) or not claims:
        return {
            "error": "action='witness' requires claims: list[{text, evidence{check,...}}]",
            "query_time": _now_iso(),
        }

    summary_text = "; ".join(str(c.get("text", ""))[:80] for c in claims[:5])
    req = fw_request(
        claim=summary_text or "unnamed claim set",
        evidence=[json.dumps(c.get("evidence", {})) for c in claims[:20]],
        source="chron_frame_witness",
    )

    try:
        payload = json.dumps({"claims": claims}).encode()
        http_req = urllib.request.Request(
            f"{FRAME_URL}/frame/witness/claims",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(http_req, timeout=15) as resp:
            frame_out = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "error": f"FRAME unreachable at {FRAME_URL}: {exc}",
            "request_logged": True,
            "request": req,
            "note": "witness request stays PENDING in frame_requests.jsonl",
            "query_time": _now_iso(),
        }

    rows = frame_out.get("rows", [])
    counts = frame_out.get("counts", {})
    n_contradicted = counts.get("CONTRADICTED", 0)
    n_verified = counts.get("VERIFIED", 0)
    n_stale = counts.get("STALE", 0)
    n_unverifiable = counts.get("UNVERIFIABLE", 0) + counts.get("EACCES", 0)

    if n_contradicted:
        verdict = "CONTESTED"
    elif n_verified:
        verdict = "CONFIRMED"
    else:
        verdict = "UNVERIFIABLE"

    if n_unverifiable == 0 and n_contradicted == 0:
        quality = "OBSERVED"
    elif n_verified or n_stale:
        quality = "INFERRED"
    else:
        quality = "SPECULATIVE"

    contradictions = [
        r.get("claim", "") for r in rows if r.get("status") == "CONTRADICTED"
    ]
    decided = n_verified + n_stale + n_contradicted + n_unverifiable
    confidence = round(n_verified / decided, 3) if decided else 0.0

    rec = fw_record(
        claim=summary_text or "unnamed claim set",
        verdict=verdict,
        evidence_quality=quality,
        contradictions=contradictions,
        confidence=confidence,
    )

    return {
        "witness_verdict": verdict,
        "evidence_quality": quality,
        "contradictions": contradictions,
        "confidence": confidence,
        "frame_counts": counts,
        "rows": rows,
        "discipline_note": frame_out.get(
            "discipline_note",
            "action-trace evidence outranks prose; FRAME never executes commands",
        ),
        "audit": {
            "request_file": "frame_requests.jsonl",
            "result_id": rec.get("result_id"),
            "frame_url": f"{FRAME_URL}/frame/witness/claims",
        },
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
    chron_events = Path("/root/chron/chron_events.json")
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


# ── /health aggregate cache (2026-10-15) ──────────────────────────────────
# /health recomputed aggregates over ~194k episodes on every call (~800ms,
# 900× slower than every other organ). Bounded fix: 60s TTL cache with honest
# sensor labeling — payload carries served/computed_at/age_s so a cached read
# can never be mistaken for a live one. Writers untouched (zero mutation risk).
_HEALTH_CACHE: dict = {"ts": 0.0, "payload": None}


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Health endpoint for federation probing."""
    import time as _time

    from starlette.responses import JSONResponse

    _now = _time.time()
    if _HEALTH_CACHE["payload"] is None or _now - _HEALTH_CACHE["ts"] > 60.0:
        store = get_store()
        preds = load_predictions()
        calibration = compute_calibration()
        active = get_active()
        ledger = get_verified(limit=10000)
        decisive = [v for v in ledger if _canonical_verdict(v) in _DECISIVE]
        # Counted from the ledger, not from calibration: an explicitly closed-untestable
        # arrow is finished-but-unscored and must never be conflated with a verifier's
        # "evidence is late". Reading calibration here reported 0 for closures that existed.
        awaiting = [v for v in ledger if _canonical_verdict(v) == VERDICT_UNVERIFIABLE]
        closed_untestable = [
            v for v in ledger if _canonical_verdict(v) == VERDICT_CLOSED_UNTESTABLE
        ]

        ad_summary = get_ad_summary()

        payload = {
            "status": "ok",
            "organ": "CHRON",
            "port": int(os.environ.get("CHRON_MCP_PORT", 18102)),
            "episodes": store.count(),
            "predictions": {
                "total": len(preds),
                "active": len(active),
                "verified": len(decisive),
                "unverifiable_awaiting_evidence": len(awaiting),
                "closed_untestable": len(closed_untestable),
                "unverifiable": len(awaiting),
                "orphan_verification_records": calibration.get("orphan_records", 0),
            },
            "calibration": {
                "total_verified": calibration.get("total", 0),
                "accuracy": calibration.get("accuracy"),
                "mean_brier": calibration.get("mean_brier"),
                # D4-PATCH-2026-10-07: surface Wilson CI + quality at /health.
                "quality": calibration.get("quality"),
                "accuracy_ci95": calibration.get("accuracy_ci95"),
                "warnings": calibration.get("warnings", []),
            },
            "attention_debt": {
                "total_ad": ad_summary.get("total_ad", 0),
                "item_count": ad_summary.get("item_count", 0),
                "growth_rate_per_day": ad_summary.get("growth_rate_per_day"),
            },
            "query_time": _now_iso(),
        }
        _HEALTH_CACHE["payload"] = payload
        _HEALTH_CACHE["ts"] = _now
        payload["cache"] = {"served": "live", "computed_at": payload["query_time"]}
    else:
        payload = dict(_HEALTH_CACHE["payload"])
        payload["cache"] = {
            "served": "cached",
            "computed_at": _HEALTH_CACHE["payload"]["query_time"],
            "age_s": round(_now - _HEALTH_CACHE["ts"], 1),
        }

    return JSONResponse(payload)


# ───────────────────────── PROMPTS (Phase 4) ─────────────────────────
# Prompts are ergonomic — they help construct intent + tool calls.
# They are NOT capability, authority, or policy. The actual operation
# requires arifOS judgment + A-FORGE execution + F13 seal.


@mcp.prompt(title="Chron Briefing")
def briefing_prompt(focus: str = "today") -> str:
    """
    Construct a temporal-briefing intent. Calls chron_temporal_briefing()
    to pull predictions-due, calibration, attention_debt, last_loop,
    and active events into one structured packet.

    After the briefing, the agent should:
    1. Call chron_predictions_due() for items needing verification NOW
    2. Call chron_attention_debt() for the backlog state
    3. Call chron_calibration_state() for epistemic health
    4. Call chron_proxy_reality_state() for the gaming detector
    """
    return (
        f"Run chron_temporal_briefing() with focus on '{focus}'. "
        "Surface: (1) what predictions are due NOW, (2) calibration accuracy "
        "and Brier score, (3) attention debt trend, (4) last loop summary, "
        "(5) active events ordered by days_until. "
        "If anything is high-consequence and due soon, name it first. "
        "Plain BM if human-facing, structured JSON if agent-facing."
    )


@mcp.prompt(title="Chron Forecast")
def forecast_prompt(horizon_days: int = 7) -> str:
    """
    Construct a forecast intent. Calls chron_active_events(audience='both')
    and chron_active_predictions() to enumerate expectations.

    Output: ordered list of (event_date, prediction, confidence, verifier).
    Flag any prediction with confidence < 0.5 or no falsifier.
    Include 'next verification' timestamp from chron_predictions_due().
    """
    return (
        f"Generate a {horizon_days}-day forward forecast. "
        "Call chron_active_events() with audience='both' and chron_active_predictions(). "
        "Call chron_predictions_due() to identify imminent verification obligations. "
        "Output: ordered list of (event_date, prediction, confidence, verifier). "
        "Flag any prediction with confidence < 0.5 or no falsifier. "
        "Include 'next verification' timestamp."
    )


@mcp.prompt(title="Chron Calibrate")
def calibrate_prompt(window: int = 30) -> str:
    """
    Construct a calibration-check intent. Calls chron_calibration_state()
    and chron_verified_predictions(limit=100).

    Group by error_type. Compute mean Brier per group.
    Identify which prediction categories are systematically over- or
    under-confident. Output actionable calibration adjustment —
    DO NOT auto-apply.
    """
    return (
        f"Run a calibration audit over the last {window} days. "
        "Call chron_calibration_state(), chron_verified_predictions(limit=100), "
        "and chron_lessons() to cross-reference what was learned. "
        "Group by error_type. Compute mean Brier per group. "
        "Identify which prediction categories are systematically over- or "
        "under-confident. Check if existing lessons address the top error modes. "
        "Output: actionable calibration adjustment proposal — DO NOT auto-apply."
    )


@mcp.prompt(title="Chron Audit")
def audit_prompt(scope: str = "store") -> str:
    """
    Construct an audit intent. Calls chron_reconcile_state(force=true),
    chron_store_stats(), and chron_proxy_reality_state().

    Verify: (1) episode_count matches JSONL line count,
    (2) invariant_held=true, (3) all active predictions have valid verify_at,
    (4) all verified records have brier_score,
    (5) proxy_reality pairs have observation coverage.

    Report any drift or invariant violation as HOLD.
    """
    return (
        f"Run a CHRON audit on scope='{scope}'. "
        "Call chron_reconcile_state(force=true), chron_store_stats(), "
        "chron_attention_debt(), and chron_proxy_reality_state(). "
        "Verify: (1) episode_count matches JSONL line count, "
        "(2) invariant_held=true, (3) all active predictions have valid verify_at, "
        "(4) all verified records have brier_score, "
        "(5) proxy_reality pairs have observation coverage > 0. "
        "Report any drift, invariant violation, or empty proxy-reality pairs as HOLD."
    )


# ───────────────────────── MAIN ─────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("CHRON_MCP_PORT", 18102))
    print(f"CHRON MCP starting on port {port} (streamable-http)")
    mcp.run(transport="streamable-http", port=port, host="127.0.0.1")
