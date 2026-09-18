#!/usr/bin/env python3
"""CHRON arifFlow Bridge — reads new receipts and creates observe episodes.

Two signal sources:
  1. JSONL tail-follow: reads new receipts from AAA logs
  2. FQ daemon poll: polls arifFlow :7073/health for STUCK/BURNING/CRITICAL

Target: /root/chron/data/episodes.jsonl (CHRON bitemporal store)
Offset: /root/chron/data/arifflow_offset.txt

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chron.chron_store import get_store

RECEIPTS_FILE = Path("/root/AAA/logs/arifflow_receipts.jsonl")
OFFSET_FILE = Path("/root/chron/data/arifflow_offset.txt")
FQ_STATE_FILE = Path("/root/chron/data/fq_last_state.json")
ARIFLOW_HEALTH = "http://127.0.0.1:7073/health"

# Events worth creating episodes for (skip noise)
WORTHY_EVENTS = {
    "FLOW_RECEIPT",
    "SEAL_COMPLETE",
    "JUDGE_VERDICT",
    "FORGE_EXECUTE",
    "OBSERVE_COMPLETE",
    "ROUTE_DECISION",
    "MEMORY_PROMOTE",
    "DEPLOY_COMPLETE",
    "HEALTH_CHECK",
    "DRIFT_DETECTED",
    "SHADOW_RENDER_COMPLETE",
    "SHADOW_INPAINT_COMPLETE",
    "CONSTITUTIONAL_CHECK",
    "EVIDENCE_COLLECTED",
    "PREDICTION_MADE",
    "VERIFICATION_COMPLETE",
    "LESSON_EXTRACTED",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_offset() -> int:
    """Load last processed line offset."""
    if not OFFSET_FILE.exists():
        return 0
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def save_offset(offset: int) -> None:
    """Save last processed line offset."""
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def receipt_to_episode(receipt: dict) -> dict | None:
    """Convert an arifflow receipt to a CHRON observe episode.

    Returns None if receipt is not worth creating an episode for.
    """
    event = receipt.get("event", "")
    timestamp = receipt.get("timestamp", receipt.get("ts", _now_iso()))

    # Filter: only create episodes for worthy events
    if event not in WORTHY_EVENTS:
        # Also check FQ signals
        if "fq" in receipt and "verdict" in receipt:
            # FQ telemetry — create episode for significant signals
            fq = receipt.get("fq", 0)
            verdict = receipt.get("verdict", "")
            if verdict in ("STUCK", "BURNING", "CRITICAL"):
                event = "FQ_SIGNAL_DRIFT"
            else:
                return None  # Skip normal FQ snapshots
        else:
            return None

    # Build observation content
    content_parts = [f"arifFlow event: {event}"]
    if receipt.get("route"):
        content_parts.append(f"route={receipt['route']}")
    if receipt.get("routed_organ"):
        content_parts.append(f"organ={receipt['routed_organ']}")
    if receipt.get("verdict"):
        content_parts.append(f"verdict={receipt['verdict']}")
    if receipt.get("diagnosis"):
        content_parts.append(f"diagnosis={receipt['diagnosis']}")
    if receipt.get("content_class"):
        content_parts.append(f"class={receipt['content_class']}")

    content = " | ".join(content_parts)

    # Determine consequence from event type
    consequence = "LOW"
    if event in ("SEAL_COMPLETE", "JUDGE_VERDICT", "DEPLOY_COMPLETE"):
        consequence = "HIGH"
    elif event in ("DRIFT_DETECTED", "FQ_SIGNAL_DRIFT", "FORGE_EXECUTE"):
        consequence = "MEDIUM"

    # Build episode
    episode = {
        "function": "observe",
        "question": "What materially changed in the federation?",
        "principal": "federation",
        "audience": "internal",
        "valid_time": timestamp,
        "observed_at": _now_iso(),
        "observations": [
            {
                "obs_id": f"obs-arifflow-{receipt.get('receipt_id', receipt.get('ts', 'unknown'))[:12]}",
                "source": f"arifflow:{event}",
                "content": content,
                "observed_at": _now_iso(),
                "truth_class": "OBS",
                "confidence": 0.9,
            }
        ],
        "claims": [],
        "selected_signals": [
            {
                "signal_id": f"sig-arifflow-{event}",
                "domain": "REALITY",
                "content": content,
                "ranking_score": 0.0,
                "truth_score": 0.0,
                "urgency_score": 0.0,
                "consequence": consequence,
                "actionability": "WATCH",
                "audience": "internal",
            }
        ],
        "rejected_signals": [],
        "predictions": [],
        "decisions": [],
        "actions": [],
        "outcomes": [],
        "output_decision": "SILENT",
        "renderer_used": "none",
        "provenance": [
            {
                "source_kind": "ORGAN",
                "source_ref": f"arifflow:{RECEIPTS_FILE}",
                "retrieved_at": _now_iso(),
                "method": "arifflow_bridge_tail_follow",
                "evidence_class": "PRIMARY",
            }
        ],
        "receipts": [receipt.get("receipt_id", "")],
    }

    return episode


def run_bridge() -> dict:
    """Run the bridge: read new receipts, create episodes."""
    if not RECEIPTS_FILE.exists():
        return {
            "status": "SKIP",
            "reason": "receipts file not found",
            "path": str(RECEIPTS_FILE),
        }

    offset = load_offset()
    store = get_store()

    # Read all lines
    with open(RECEIPTS_FILE) as f:
        lines = f.readlines()

    total_lines = len(lines)
    new_lines = lines[offset:]

    if not new_lines:
        return {
            "status": "OK",
            "new_receipts": 0,
            "total_lines": total_lines,
            "offset": offset,
        }

    episodes_created = 0
    skipped = 0

    for line in new_lines:
        line = line.strip()
        if not line:
            continue

        try:
            receipt = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        episode = receipt_to_episode(receipt)
        if episode:
            store.append(episode)
            episodes_created += 1
        else:
            skipped += 1

    # Save new offset
    save_offset(total_lines)

    return {
        "status": "OK",
        "new_receipts": len(new_lines),
        "episodes_created": episodes_created,
        "skipped": skipped,
        "total_lines": total_lines,
        "new_offset": total_lines,
        "store_total": store.count(),
    }


# ───────────────────────── FQ DAEMON POLL ─────────────────────────


def load_fq_state() -> dict:
    """Load last known FQ state to detect transitions."""
    if not FQ_STATE_FILE.exists():
        return {}
    try:
        return json.loads(FQ_STATE_FILE.read_text())
    except Exception:
        return {}


def save_fq_state(state: dict) -> None:
    """Save current FQ state."""
    FQ_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FQ_STATE_FILE.write_text(json.dumps(state, default=str))


def poll_fq_daemon() -> dict:
    """Poll arifFlow daemon for FQ signals. Create episodes on significant transitions.

    Triggers:
      - Any actor enters STUCK (fq=0, executions without verification)
      - Any actor enters BURNING (execution outruns verification)
      - Global FQ crosses thresholds (was OK → now STUCK, or vice versa)
      - System-level verdict changes (OPTIMAL → DEGRADED → CRITICAL)
    """
    try:
        result = subprocess.run(
            ["curl", "-sf", "-m", "5", ARIFLOW_HEALTH],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"status": "SKIP", "reason": "arifflow unreachable"}
        health = json.loads(result.stdout)
    except Exception as e:
        return {"status": "SKIP", "reason": str(e)}

    fq_data = health.get("fq", {})
    if not fq_data:
        return {"status": "SKIP", "reason": "no fq data"}

    # Load previous state
    prev = load_fq_state()
    store = get_store()
    episodes_created = 0

    # Check global FQ
    global_fq = fq_data.get("quotient", 0)
    global_verdict = fq_data.get("legacy_verdict", "UNKNOWN")
    prev_global = prev.get("global_verdict", "UNKNOWN")

    # Check per-actor signals
    per_actor = fq_data.get("per_actor", {})
    significant_actors = []

    for actor_id, actor_data in per_actor.items():
        verdict = actor_data.get("verdict", "UNKNOWN")
        diagnosis = actor_data.get("diagnosis", "")
        held = actor_data.get("held", False)
        fq_val = actor_data.get("quotient")

        # Track significant state changes
        prev_actor = prev.get("actors", {}).get(actor_id, {})
        prev_verdict = (
            prev.get("actors", {}).get(actor_id, {}).get("verdict", "UNKNOWN")
        )

        # Significant transitions
        is_significant = False
        if verdict in ("STUCK", "BURNING") and prev_verdict not in ("STUCK", "BURNING"):
            is_significant = True
        elif verdict == "UNKNOWN" and prev_verdict not in ("UNKNOWN",):
            is_significant = True
        elif held and not prev_actor.get("held", False):
            is_significant = True
        elif (
            diagnosis == "EXECUTION DOMINANCE"
            and prev_actor.get("diagnosis") != "EXECUTION DOMINANCE"
        ):
            is_significant = True

        if is_significant:
            significant_actors.append(
                {
                    "actor_id": actor_id,
                    "verdict": verdict,
                    "diagnosis": diagnosis,
                    "fq": fq_val,
                    "held": held,
                    "execute": actor_data.get("execute", 0),
                    "verify": actor_data.get("verify", 0),
                }
            )

            # Create episode
            content = (
                f"arifFlow FQ signal: actor={actor_id} "
                f"verdict={verdict} diagnosis={diagnosis} "
                f"fq={fq_val} held={held} "
                f"execute={actor_data.get('execute', 0)} "
                f"verify={actor_data.get('verify', 0)}"
            )

            consequence = "LOW"
            if verdict in ("STUCK", "BURNING"):
                consequence = "HIGH"
            elif held:
                consequence = "MEDIUM"

            episode = {
                "function": "observe",
                "question": "What materially changed in the federation's metabolic state?",
                "principal": "federation",
                "audience": "internal",
                "valid_time": _now_iso(),
                "observed_at": _now_iso(),
                "observations": [
                    {
                        "obs_id": f"obs-fq-{actor_id}-{datetime.now(timezone.utc).strftime('%H%M%S')}",
                        "source": f"arifflow:fq_poll:{actor_id}",
                        "content": content,
                        "observed_at": _now_iso(),
                        "truth_class": "OBS",
                        "confidence": 0.95,
                    }
                ],
                "claims": [],
                "selected_signals": [
                    {
                        "signal_id": f"sig-fq-{actor_id}",
                        "domain": "REALITY",
                        "content": content,
                        "ranking_score": 0.0,
                        "truth_score": 0.0,
                        "urgency_score": 0.0,
                        "consequence": consequence,
                        "actionability": "WATCH",
                        "audience": "internal",
                    }
                ],
                "rejected_signals": [],
                "predictions": [],
                "decisions": [],
                "actions": [],
                "outcomes": [],
                "output_decision": "SILENT",
                "renderer_used": "none",
                "provenance": [
                    {
                        "source_kind": "ORGAN",
                        "source_ref": f"arifflow:fq_poll:{ARIFLOW_HEALTH}",
                        "retrieved_at": _now_iso(),
                        "method": "arifflow_fq_daemon_poll",
                        "evidence_class": "PRIMARY",
                    }
                ],
                "receipts": [],
            }

            store.append(episode)
            episodes_created += 1

    # Save current state for next comparison
    save_fq_state(
        {
            "global_fq": global_fq,
            "global_verdict": global_verdict,
            "actors": {
                aid: {
                    "verdict": ad.get("verdict"),
                    "diagnosis": ad.get("diagnosis"),
                    "held": ad.get("held"),
                }
                for aid, ad in per_actor.items()
            },
            "updated_at": _now_iso(),
        }
    )

    return {
        "status": "OK",
        "global_fq": global_fq,
        "global_verdict": global_verdict,
        "actors_tracked": len(per_actor),
        "significant_transitions": len(significant_actors),
        "episodes_created": episodes_created,
        "transitions": significant_actors,
    }


# ───────────────────── PUSH CHRON → ARIFLOW ─────────────────────

ARIFLOW_INGEST = "http://127.0.0.1:7073/ingest"


def push_verification_result(prediction: dict, result: dict) -> bool:
    """Push a CHRON verification result into arifFlow via /ingest.

    Makes temporal intelligence visible to federation monitoring.
    Called after chron_verify.py completes a prediction check.

    Args:
        prediction: the prediction dict from predictions.jsonl
        result: the verification result (verified, brier, error, etc.)

    Returns True if ingested successfully.
    """
    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "actor_id": "chron",
        "session_id": f"chron-verify-{prediction.get('prediction_id', 'unknown')[:12]}",
        "step_type": "Verify",
        "epistemic_label": "Derivation",
        "cost_ns": 0,
        "step_number": 1,
        "created_at": _now_iso(),
        "floor_verdict": "Pass",
        "cooling_decision": "None",
        "payload": {
            "prediction_id": prediction.get("prediction_id"),
            "claim": prediction.get("claim", "")[:200],
            "expected_outcome": prediction.get("expected_outcome", "")[:200],
            "observed_outcome": result.get("observed_outcome", "")[:200],
            "confidence": prediction.get("confidence"),
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "brier_score": result.get("brier_score"),
            "verified_correct": result.get("verified_correct"),
            "source": prediction.get("source"),
            "verify_at": prediction.get("verify_at"),
        },
        "intent_reason": "CHRON temporal verification — prediction met reality",
        "expected_outcome": f"Prediction {prediction.get('prediction_id', '?')} verified against observed outcome",
    }

    try:
        import urllib.request

        req = urllib.request.Request(
            ARIFLOW_INGEST,
            data=json.dumps(receipt).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            fq = data.get("fq", {})
            return data.get("status") == "ok" or "fq" in data
    except Exception:
        return False


def push_loop_closure(cycle_id: str, arrows_status: dict, lessons: list) -> bool:
    """Push a CHRON loop closure event into arifFlow.

    Called by chron_loop_close.py after completing a full metabolic cycle.

    Args:
        cycle_id: the loop cycle identifier
        arrows_status: dict of arrow name → PASS/FAIL/SKIP
        lessons: list of lesson dicts extracted this cycle

    Returns True if ingested successfully.
    """
    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "actor_id": "chron",
        "session_id": f"chron-loop-{cycle_id[:12]}",
        "step_type": "Verify",
        "epistemic_label": "Derivation",
        "cost_ns": 0,
        "step_number": 1,
        "created_at": _now_iso(),
        "floor_verdict": "Pass",
        "cooling_decision": "None",
        "payload": {
            "cycle_id": cycle_id,
            "arrows": arrows_status,
            "lessons_count": len(lessons),
            "lessons": [
                {
                    "lesson": (l.get("lesson") or "")[:150],
                    "source": l.get("source_prediction_id", "?"),
                }
                for l in lessons[:5]
            ],
        },
        "intent_reason": "CHRON loop closure — metabolic cycle complete",
        "expected_outcome": "Temporal intelligence cycle registered in federation flow",
    }

    try:
        import urllib.request

        req = urllib.request.Request(
            ARIFLOW_INGEST,
            data=json.dumps(receipt).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ok" or "fq" in data
    except Exception:
        return False


def main() -> int:
    print(f"CHRON arifFlow Bridge — {_now_iso()[:16]}")
    print()

    # 1. JSONL tail-follow
    result = run_bridge()
    print(f"  JSONL: {result['status']}")
    if result.get("new_receipts", 0) > 0:
        print(
            f"    New: {result['new_receipts']}  Episodes: {result.get('episodes_created', 0)}"
        )
    else:
        print(f"    No new (offset={result.get('offset', 0)})")

    # 2. FQ daemon poll
    fq_result = poll_fq_daemon()
    print(f"  FQ poll: {fq_result['status']}")
    if fq_result.get("significant_transitions", 0) > 0:
        print(
            f"    Transitions: {fq_result['significant_transitions']}  Episodes: {fq_result.get('episodes_created', 0)}"
        )
        for t in fq_result.get("transitions", []):
            print(f"      {t['actor_id']}: {t['verdict']} ({t['diagnosis']})")
    elif fq_result.get("global_fq") is not None:
        print(
            f"    FQ={fq_result['global_fq']}  Verdict={fq_result.get('global_verdict')}  Actors={fq_result.get('actors_tracked')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
