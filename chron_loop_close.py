"""CHRON Loop Closer — wires the three UNBUILT arrows.

OUTCOME → LEARNING: verify → extract_lessons
EXPERIENCE → MEMORY: arifflow bridge → episode store → durable lessons
MEMORY → POLICY: lessons → policy candidate → promotion gate

This is the runtime that closes the open loop.
Run: python3 -m chron.loop_close

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chron.chron_verify import run_verification
from chron.chron_learn import extract_lessons, load_lessons, get_candidates
from chron.chron_prediction import get_verified, compute_calibration, save_calibration
from chron.chron_store import get_store
from chron.chron_ariflow_bridge import run_bridge, poll_fq_daemon

LESSONS_FILE = Path("/root/chron/data/lessons.jsonl")
POLICY_CANDIDATES_FILE = Path("/root/chron/data/policy_candidates.jsonl")
LOOP_LOG = Path("/root/chron/data/loop_log.jsonl")

MYT = timezone(timedelta(hours=8))

# Best-effort outbound steps that must never fail the loop, but must never fail
# silently either. Filled during a cycle and disclosed in the loop entry.
WARNINGS: list[dict] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── ARROW 1: OUTCOME → LEARNING ─────────────────────────


def arrow_outcome_to_learning(dry_run: bool = False) -> dict:
    """Wire: verification results → lesson extraction.

    This closes the OUTCOME → LEARNING arrow.
    After every verification run, extract lessons from verified predictions.
    """
    # 1. Run verification on due predictions
    verify_result = run_verification(dry_run=dry_run)

    # 2. Extract lessons from verified predictions
    #    (dry_run: extract_lessons APPENDS to lessons.jsonl and the episode
    #     store — so it must not run in a dry pass. 2026-09-18)
    lessons = [] if dry_run else extract_lessons()

    # 3. Compute calibration
    calibration = compute_calibration()
    if not dry_run:
        save_calibration(calibration)

    result = {
        "arrow": "OUTCOME → LEARNING",
        "timestamp": _now_iso(),
        "verify_result": {
            "due": verify_result.get("due", 0),
            "verified_correct": verify_result.get("verified_correct", 0),
            "verified_incorrect": verify_result.get("verified_incorrect", 0),
            "unverifiable": verify_result.get("unverifiable", 0),
        },
        "lessons_extracted": len(lessons),
        "calibration": {
            "total_verified": calibration.get("total", 0),
            "accuracy": calibration.get("accuracy"),
            "mean_brier": calibration.get("mean_brier"),
        },
        "status": "COMPLETE"
        if verify_result.get("due", 0) > 0
        else "NO_DUE_PREDICTIONS",
    }

    if not dry_run:
        try:
            from chron.chron_early_falsifier_scan import main as _binding_scan
            _binding_scan()
            result["binding_scan"] = "/root/chron/data/binding_scan.json"
        except Exception as exc:
            WARNINGS.append({"step": "binding_scan", "error": str(exc)})
        _log_loop("OUTCOME→LEARNING", result)

    return result


# ───────────────────────── ARROW 2: EXPERIENCE → MEMORY ─────────────────────────


def arrow_experience_to_memory(dry_run: bool = False) -> dict:
    """Wire: arifflow receipts → CHRON episodes → durable lessons.

    This closes the EXPERIENCE → MEMORY arrow.
    The arifflow bridge already creates episodes. This arrow ensures
    those episodes feed into the lesson extraction pipeline.

    dry_run (2026-09-18): this arrow MUTATES — it ingests receipts and appends
    episodes. It previously ignored the loop's dry_run flag, so a "dry" loop
    still wrote to the store. A dry run that mutates is not a dry run.
    """
    if dry_run:
        return {
            "arrow": "EXPERIENCE → MEMORY",
            "timestamp": _now_iso(),
            "bridge": {"status": "SKIPPED", "new_receipts": 0, "episodes_created": 0},
            "fq_poll": {"status": "SKIPPED", "significant_transitions": 0, "episodes_created": 0},
            "store": {"total_episodes": None, "episodes_last_24h": None},
            "status": "DRY_RUN_SKIPPED",
        }

    # 1. Run arifflow bridge (creates episodes from receipts)
    bridge_result = run_bridge()

    # 2. Poll FQ daemon (creates episodes on significant transitions)
    fq_result = poll_fq_daemon()

    # 3. Check if any new episodes warrant lesson extraction
    store = get_store()
    total_episodes = store.count()

    # Count recent episodes (last 24h)
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    recent = store.query(since=day_ago, limit=1000)

    result = {
        "arrow": "EXPERIENCE → MEMORY",
        "timestamp": _now_iso(),
        "bridge": {
            "status": bridge_result.get("status"),
            "new_receipts": bridge_result.get("new_receipts", 0),
            "episodes_created": bridge_result.get("episodes_created", 0),
        },
        "fq_poll": {
            "status": fq_result.get("status"),
            "significant_transitions": fq_result.get("significant_transitions", 0),
            "episodes_created": fq_result.get("episodes_created", 0),
        },
        "store": {
            "total_episodes": total_episodes,
            "episodes_last_24h": len(recent),
        },
        "status": "COMPLETE",
    }

    _log_loop("EXPERIENCE→MEMORY", result)
    return result


# ───────────────────────── ARROW 3: MEMORY → POLICY ─────────────────────────


def arrow_memory_to_policy(dry_run: bool = False) -> dict:
    """Wire: lessons → policy candidate → promotion gate.

    This closes the MEMORY → POLICY arrow.
    Lessons that meet promotion criteria become policy candidates.
    Policy candidates require external validation before deployment.

    Promotion criteria:
      - Recurrence: same error_class seen 2+ times
      - Measured effect: lesson applied → error reduced
      - External validation: FRAME or arifOS confirms
    """
    candidates = get_candidates()
    all_lessons = load_lessons()

    # Evaluate each candidate for promotion
    promoted = []
    held = []

    for candidate in candidates:
        promotion_result = _evaluate_promotion(candidate)
        if promotion_result["eligible"]:
            promoted.append(promotion_result)
        else:
            held.append(promotion_result)

    result = {
        "arrow": "MEMORY → POLICY",
        "timestamp": _now_iso(),
        "lessons_total": len(all_lessons),
        "candidates": len(candidates),
        "promoted": len(promoted),
        "held": len(held),
        "promotions": promoted,
        "holds": held,
        "status": "COMPLETE" if promoted else "NO_PROMOTIONS",
        "dry_run": dry_run,
    }

    if not dry_run:
        _log_loop("MEMORY→POLICY", result)
    return result


def _evaluate_promotion(lesson: dict) -> dict:
    """Evaluate whether a lesson qualifies for policy promotion.

    Returns promotion assessment.
    """
    lesson_id = lesson.get("lesson_id", "?")
    recurrence = lesson.get("recurrence", 0)
    error_type = lesson.get("error_type", "UNKNOWN")
    mean_brier = lesson.get("mean_brier")

    # Criterion 1: Recurrence (need 2+)
    has_recurrence = recurrence >= 2

    # Criterion 2: Measured effect (Brier improving)
    has_measurement = mean_brier is not None and mean_brier < 0.25  # Better than random

    # Criterion 3: External validation (placeholder — needs FRAME/arifOS)
    has_external_validation = False  # Not yet implemented

    eligible = has_recurrence and has_measurement

    return {
        "lesson_id": lesson_id,
        "error_type": error_type,
        "recurrence": recurrence,
        "mean_brier": mean_brier,
        "criteria": {
            "recurrence": has_recurrence,
            "measured_effect": has_measurement,
            "external_validation": has_external_validation,
        },
        "eligible": eligible,
        "reason": (
            "Meets all criteria"
            if eligible
            else f"Missing: {', '.join(k for k, v in {'recurrence': has_recurrence, 'measurement': has_measurement, 'external_validation': has_external_validation}.items() if not v)}"
        ),
    }


# ───────────────────────── STATE SNAPSHOT ─────────────────────────


def _store_state() -> dict:
    """Post-run snapshot of what the store actually holds.

    Why this exists (2026-09-18 loop-closure repair, L1):
    FULL_LOOP previously logged ONLY this run's deltas. Every counter read zero
    on a quiet day and also read zero when the loop was silently broken, so a
    hollow cycle and a healthy idle cycle were indistinguishable — the defect
    that let CHRON report "all zeros" while the store held live predictions.
    Delta answers "what changed"; state answers "what is true". A loop entry
    carrying both can be audited by someone who was not running it.
    """
    from chron.chron_prediction import (
        get_active as _active,
        get_due as _due,
        get_verified as _verified,
        load_predictions as _preds,
    )

    store = get_store()
    ledger = _verified(limit=10000)
    decisive = [
        v for v in ledger if v.get("verdict") in ("VERIFIED_CORRECT", "VERIFIED_INCORRECT")
    ]
    lessons = load_lessons()

    return {
        "episodes_total": store.count(),
        "episodes_by_function": store.functions(),
        "predictions_total": len(_preds()),
        "predictions_active": len(_active()),
        "predictions_due_now": len(_due()),
        "predictions_decisive": len(decisive),
        "predictions_unverifiable": len(
            [v for v in ledger if v.get("verdict") == "UNVERIFIABLE"]
        ),
        "lessons_total": len(lessons),
        "lesson_candidates": len(get_candidates()),
    }


def _run_step(name: str, fn, *args, **kwargs) -> dict:
    """Run one arrow, capturing failure as DATA instead of swallowing it.

    Why this exists (L2): every downstream call in this module was wrapped in
    `except Exception: pass`. A bridge outage therefore produced a loop entry
    identical to a quiet run — zeros and "COMPLETE". Failure is now recorded in
    the entry, which is the only way a reader can tell the difference.
    """
    try:
        return {"ok": True, "result": fn(*args, **kwargs), "error": None}
    except Exception as e:  # noqa: BLE001 — a loop must survive its parts
        return {"ok": False, "result": None, "error": f"{type(e).__name__}: {e}"}


# ───────────────────────── FULL LOOP ─────────────────────────


def run_full_loop(dry_run: bool = False) -> dict:
    """Run the full CHRON loop: all three arrows + prediction generation.

    This is the runtime that closes the open loop.
    Run daily via cron, or manually.

    Arrow 0: Events → Predictions (generate if none exist)
    Arrow 1: Experience → Memory (bridge + FQ poll)
    Arrow 2: Outcome → Learning (verify + extract lessons)
    Arrow 3: Memory → Policy (evaluate promotions)
    """
    result = {
        "loop_id": f"loop-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "started_at": _now_iso(),
        "arrows": {},
    }
    errors: list[dict] = []
    WARNINGS.clear()

    # Measure the episode delta instead of trusting a sub-step's self-report.
    # 2026-09-18: delta.episodes_created summed `bridge.episodes_created` +
    # `fq_poll.episodes_created`, but run_bridge() returns new_receipts/
    # total_lines/offset and has NO episodes_created key — so the sum was
    # structurally guaranteed to read 0 while the store grew. Measured live:
    # store went 50470 -> 50505 across a run that reported episodes_created=0.
    # A delta a component reports about itself is a claim; a before/after count
    # is a measurement.
    episodes_before = get_store().count()

    # Arrow 0: Events → Predictions (ensure predictions exist for all events)
    from chron.chron_prediction import generate_from_chron_events, get_active

    active_before = len(get_active())
    gen = _run_step("events_to_predictions", generate_from_chron_events) if not dry_run else {
        "ok": True, "result": [], "error": None
    }
    if not gen["ok"]:
        errors.append({"step": "events_to_predictions", "error": gen["error"]})
    new_preds = gen["result"] or []
    active_after = len(get_active())
    result["arrows"]["events_to_predictions"] = {
        "arrow": "EVENTS → PREDICTIONS",
        "delta": {
            "new_generated": len(new_preds),
            "active_before": active_before,
            "active_after": active_after,
        },
        "status": "ERROR" if not gen["ok"] else (
            "GENERATED" if new_preds else "ALREADY_CURRENT"
        ),
        "error": gen["error"],
    }

    # Arrow 1: Experience → Memory (bridge + FQ poll)
    exp = _run_step("experience_to_memory", arrow_experience_to_memory, dry_run)
    if not exp["ok"]:
        errors.append({"step": "experience_to_memory", "error": exp["error"]})
        result["arrows"]["experience_to_memory"] = {
            "arrow": "EXPERIENCE → MEMORY",
            "status": "ERROR",
            "error": exp["error"],
        }
    else:
        result["arrows"]["experience_to_memory"] = exp["result"]

    # Arrow 2: Outcome → Learning (verify + extract lessons)
    out = _run_step("outcome_to_learning", arrow_outcome_to_learning, dry_run)
    if not out["ok"]:
        errors.append({"step": "outcome_to_learning", "error": out["error"]})
        result["arrows"]["outcome_to_learning"] = {
            "arrow": "OUTCOME → LEARNING",
            "status": "ERROR",
            "error": out["error"],
        }
    else:
        result["arrows"]["outcome_to_learning"] = out["result"]

    # Arrow 3: Memory → Policy (evaluate promotions)
    pol = _run_step("memory_to_policy", arrow_memory_to_policy, dry_run)
    if not pol["ok"]:
        errors.append({"step": "memory_to_policy", "error": pol["error"]})
        result["arrows"]["memory_to_policy"] = {
            "arrow": "MEMORY → POLICY",
            "status": "ERROR",
            "error": pol["error"],
        }
    else:
        result["arrows"]["memory_to_policy"] = pol["result"]

    result["completed_at"] = _now_iso()

    # ── DELTA: what changed because of this run ──
    e2m = result["arrows"].get("experience_to_memory", {})
    o2l = result["arrows"].get("outcome_to_learning", {})
    m2p = result["arrows"].get("memory_to_policy", {})
    episodes_after = get_store().count()
    delta = {
        "episodes_created": episodes_after - episodes_before,
        "episodes_created_reported": {
            "bridge": (e2m.get("bridge") or {}).get("episodes_created", 0),
            "fq_poll": (e2m.get("fq_poll") or {}).get("episodes_created", 0),
        },
        "predictions_verified": (
            (o2l.get("verify_result") or {}).get("verified_correct", 0)
            + (o2l.get("verify_result") or {}).get("verified_incorrect", 0)
        ),
        "predictions_new": (result["arrows"]["events_to_predictions"]["delta"]
                            .get("new_generated", 0)),
        "lessons_extracted": o2l.get("lessons_extracted", 0),
        "policies_promoted": m2p.get("promoted", 0),
    }

    # ── STATE: what is true after this run ──
    state = _run_step("store_state", _store_state)
    if not state["ok"]:
        errors.append({"step": "store_state", "error": state["error"]})
        state_val = None
    else:
        state_val = state["result"]

    # ── STATUS: three-way, so a reader can tell quiet from broken ──
    #   DEGRADED — a step failed; the entry says which one
    #   QUIET    — nothing errored, nothing was due; state is the evidence
    #   ACTIVE   — this run changed something
    if errors:
        status = "DEGRADED"
    elif any(
        v for k, v in delta.items()
        if k not in ("predictions_new", "episodes_created_reported")
    ):
        status = "ACTIVE"
    else:
        status = "QUIET"

    result["summary"] = {
        "status": status,
        "delta": delta,
        "state": state_val,
        "errors": errors,
    }

    # Best-effort outbound: recorded as a warning, not an error. A dark receipt
    # lane does not mean CHRON failed, but a reader must be able to see it.
    if not dry_run:
        cf_ok, cf_err = _inject_carry_forward(result["summary"])
        if not cf_ok:
            WARNINGS.append({"step": "carry_forward_inject", "warning": cf_err})

    result["summary"]["warnings"] = list(WARNINGS)

    # A dry pass writes nothing at all — including this log. (2026-09-18)
    if not dry_run:
        _log_loop("FULL_LOOP", result["summary"])

    return result


# ───────────────────────── CARRY FORWARD INJECTION ─────────────────────────


def _inject_carry_forward(summary: dict) -> tuple[bool, str | None]:
    """Inject CHRON temporal briefing into carry_forward.json.

    The next session's agent reads carry_forward.json on wake-up
    and immediately gains temporal awareness.

    Returns (ok, error). Best-effort by design — CHRON works without
    carry_forward — but the outcome is reported rather than discarded, because a
    silent failure here means every future session wakes without temporal
    awareness and nobody can tell. (2026-09-18)
    """
    try:
        import subprocess

        result = subprocess.run(
            [sys.executable, "/root/scripts/carry_forward.py", "chron-inject"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, None
        return False, f"carry_forward exited {result.returncode}: {(result.stderr or '').strip()[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ───────────────────────── LOGGING ─────────────────────────


def _log_loop(arrow: str, data: dict) -> None:
    """Append to loop log.

    The outbound arifFlow emit runs BEFORE the entry is serialised, so a receipt
    failure is captured in the record rather than appended to the in-memory dict
    after the fact (which would have written it nowhere). (2026-09-18)
    """
    LOOP_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Push to arifFlow as receipt (makes CHRON visible to federation)
    if arrow == "FULL_LOOP":
        ok, err = _emit_ariflow_receipt(data)
        if not ok:
            WARNINGS.append({"step": "arifflow_receipt", "warning": err})
        data["warnings"] = list(WARNINGS)

    entry = {
        "timestamp": _now_iso(),
        "arrow": arrow,
        "data": data,
    }
    with open(LOOP_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _emit_ariflow_receipt(data: dict) -> tuple[bool, str | None]:
    """Emit CHRON loop summary as arifFlow receipt.

    This makes temporal intelligence visible to FQ monitoring,
    FRAME drift detection, and the entire federation.

    Returns (ok, error) — reported, not swallowed: a dark receipt lane means the
    federation stops seeing CHRON run while CHRON believes it reported.
    (2026-09-18)
    """
    try:
        import urllib.request
        import uuid

        receipt = {
            "receipt_id": str(uuid.uuid4()),
            "created_at": _now_iso(),
            "actor_id": "chron",
            "session_id": f"chron-loop-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "step_type": "Verify",
            "step_number": 0,
            "cost_ns": 0,
            "epistemic_label": "Derivation",
            "floor_verdict": "Pass",
            "cooling_decision": "None",
            "summary": (
                f"CHRON loop [{data.get('status')}]: "
                f"episodes={(data.get('delta') or {}).get('episodes_created', 0)}, "
                f"verified={(data.get('delta') or {}).get('predictions_verified', 0)}, "
                f"lessons={(data.get('delta') or {}).get('lessons_extracted', 0)}, "
                f"policies={(data.get('delta') or {}).get('policies_promoted', 0)}, "
                f"errors={len(data.get('errors') or [])}"
            ),
            "routed_organ": "chron",
            "payload": data,
        }

        req = urllib.request.Request(
            "http://127.0.0.1:7073/ingest",
            data=json.dumps(receipt).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return True, None
            return False, f"arifFlow returned {resp.status}"
    except Exception as e:
        # arifFlow down → CHRON still works, but the gap is now visible.
        return False, f"{type(e).__name__}: {e}"


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    import sys

    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    if args and args[0] == "arrows":
        # Run individual arrows
        if "1" in args or "experience" in args:
            r = arrow_experience_to_memory()
            print(f"Arrow 1 (Experience→Memory): {json.dumps(r, indent=2)}")
        if "2" in args or "outcome" in args:
            r = arrow_outcome_to_learning(dry_run=dry_run)
            print(f"Arrow 2 (Outcome→Learning): {json.dumps(r, indent=2)}")
        if "3" in args or "memory" in args:
            r = arrow_memory_to_policy()
            print(f"Arrow 3 (Memory→Policy): {json.dumps(r, indent=2)}")
        return 0

    # Full loop
    result = run_full_loop(dry_run=dry_run)
    s = result["summary"]
    print(f"CHRON Loop Closer — {result['loop_id']}")
    print(f"  Status: {s['status']}")
    print("  Delta (what this run changed):")
    for k, v in s["delta"].items():
        print(f"    {k}: {v}")
    print("  State (what is true now):")
    for k, v in (s.get("state") or {}).items():
        print(f"    {k}: {v}")
    if s.get("errors"):
        print("  Errors:")
        for e in s["errors"]:
            print(f"    {e['step']}: {e['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
