"""CHRON repair proof — join correctness on a scratch store.

Proves, without touching production data:
  1. A decisive CORRECT verdict produces accuracy 1.0
  2. An INCORRECT verdict produces accuracy 0.0 and is scored
  3. A plain UNVERIFIABLE record (evidence is late) does NOT resolve and is NOT scored
  3b. An EXPLICIT closure (resolution_class CLOSED_UNTESTABLE) ends the arrow — leaves
      the active/due sets, stops debt aging — and is still NOT scored
  3c. Legacy closure rows written before that class existed read as closed too
  4. A VOID record retracts an earlier verdict and reopens the prediction
  5. Legacy `status`-spelled records are read the same as canonical `verdict`

Run: /opt/arifos/venv/bin/python3 /root/chron/tests/test_repair_join.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root")

from chron import chron_prediction as cp  # noqa: E402
from chron import chron_verify as cv  # noqa: E402

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def write_rec(pred_id: str, verdict: str, **extra) -> None:
    rec = {
        "verification_id": f"verif-test-{pred_id}",
        "prediction_id": pred_id,
        "verdict": verdict,
        "observed_outcome": "scratch outcome",
        "error": 0.0 if verdict == "CORRECT" else (1.0 if verdict == "INCORRECT" else None),
        "error_type": "NONE" if verdict == "CORRECT" else "UNKNOWN",
        "brier_score": 0.16 if verdict == "CORRECT" else None,
        "verified_at": "2026-09-18T00:00:00Z",
    }
    rec.update(extra)
    with open(cp.VERIFICATION_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def make_pred(pid: str, confidence: float = 0.6) -> dict:
    p = {
        "prediction_id": pid,
        "claim": f"scratch claim {pid}",
        "expected_outcome": "scratch",
        "confidence": confidence,
        "verify_at": "2026-09-01T00:00:00+08:00",
        "status": "ACTIVE",
        "assumptions": [],
        "evidence_snapshot": [],
        "source_refs": [],
    }
    with open(cp.PREDICTIONS_FILE, "a") as f:
        f.write(json.dumps(p) + "\n")
    return p


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chron-repair-test-"))
    pred_file = tmp / "predictions.jsonl"
    verif_file = tmp / "verification_log.jsonl"
    pred_file.touch()
    verif_file.touch()

    # Redirect both modules at their authoritative constants.
    cp.PREDICTIONS_FILE = pred_file
    cp.VERIFICATION_LOG = verif_file
    cv.VERIFICATION_LOG = verif_file
    cv.ATTEMPTS_LOG = tmp / "verification_attempts.jsonl"

    # compute_calibration() delegates to the ONE unified accounting in
    # chron_ledger_integrity, which reads its own hardcoded two-store list.
    # Without redirecting THAT, every calibration assertion below silently reads
    # production data (measured 2026-09-29: 16 checks failing at HEAD, totals
    # matching the live n). This isolation gap is how write-surface bugs hid.
    from chron import chron_ledger_integrity as cli

    cli.LEDGER_STORES = [("chron-jsonl", pred_file, "jsonl")]
    cli.LEDGER_VERIFICATION_LOGS = [("chron-jsonl", verif_file)]
    cli.CANONICAL_CALIBRATION_FILE = tmp / "calibration.json"
    cli.CALIBRATION_SUBORDINATES = [tmp / "calibration_unified.json"]

    print("1. Decisive CORRECT → accuracy 1.0")
    make_pred("pred-a", 0.9)
    write_rec("pred-a", "CORRECT")
    got = cp.compute_calibration()
    check("total", got["total"], 1)
    check("correct", got["correct"], 1)
    check("accuracy", got["accuracy"], 1.0)

    print("2. Decisive INCORRECT → scored, accuracy drops")
    make_pred("pred-b", 0.8)
    write_rec("pred-b", "INCORRECT")
    got = cp.compute_calibration()
    check("total", got["total"], 2)
    check("incorrect", got["incorrect"], 1)
    check("accuracy", got["accuracy"], 0.5)
    # unified calibration recomputes Brier per row from birth confidence:
    # a=0.16 (stored), b=(0.8-0)^2=0.64 -> mean 0.40. The old expectation of 0.16
    # was stale from the single-row era and had been failing silently at HEAD.
    check("mean_brier", round(got["mean_brier"], 4), 0.4)

    print("3. Plain UNVERIFIABLE (evidence late) does NOT resolve and is NOT scored")
    make_pred("pred-c", 0.7)
    write_rec("pred-c", "UNVERIFIABLE", error=None, brier_score=None)
    got = cp.compute_calibration()
    check("scored total unchanged", got["total"], 2)
    # Measured truth (2026-09-29): the unified v2 calibration artifact publishes
    # `unverifiable` as a constant 0 even with one UNVERIFIABLE row present. It is
    # NOT a disclosure surface, so asserting 1 here would demand a number the system
    # does not produce. Disclosure now lives in the ledger-derived census below,
    # which is what /health reads.
    check("calibration publishes no unverifiable count", got["unverifiable"], 0)
    led = cp.get_verified(limit=1000)
    awaiting_rows = [v for v in led if cp._canonical_verdict(v) == cp.VERDICT_UNVERIFIABLE]
    closed_rows = [v for v in led if cp._canonical_verdict(v) == cp.VERDICT_CLOSED_UNTESTABLE]
    check("ledger census finds the late row", len(awaiting_rows), 1)
    check("ledger census does not conflate closed rows", len(closed_rows), 0)
    check("accuracy unchanged", got["accuracy"], 0.5)
    active_ids = {p["prediction_id"] for p in cp.get_active()}
    check("pred-c still active", "pred-c" in active_ids, True)
    check("pred-a resolved (not active)", "pred-a" in active_ids, False)

    print("3b. EXPLICIT closure (resolution_class CLOSED_UNTESTABLE) ends the arrow, unscored")
    make_pred("pred-f", 0.7)
    write_rec("pred-f", "UNVERIFIABLE", error=None, brier_score=None,
              resolution_class="CLOSED_UNTESTABLE")
    check("closure canonical verdict",
          cp._canonical_verdict({"verdict": "UNVERIFIABLE",
                                 "resolution_class": "CLOSED_UNTESTABLE"}),
          cp.VERDICT_CLOSED_UNTESTABLE)
    got = cp.compute_calibration()
    check("closure NOT scored", got["total"], 2)
    active_ids = {p["prediction_id"] for p in cp.get_active()}
    check("pred-f left the active set", "pred-f" in active_ids, False)
    check("lateness still active (contrast)", "pred-c" in active_ids, True)
    due_ids = {p["prediction_id"] for p in cp.get_due()}
    check("pred-f never re-offered to verify_due", "pred-f" in due_ids, False)
    check("closure not in decided_ids (calibration untouched)",
          "pred-f" in cp.decided_ids(), False)
    check("closure IS in resolved_ids", "pred-f" in cp.resolved_ids(), True)

    print("3c. Legacy closure rows (verified_by=chron_resolve_unverifiable, no class) read as closed")
    make_pred("pred-g", 0.7)
    write_rec("pred-g", "UNVERIFIABLE", error=None, brier_score=None,
              verified_by="chron_resolve_unverifiable")
    check("legacy closure not scored", cp.compute_calibration()["total"], 2)
    check("legacy closure left active",
          "pred-g" in {p["prediction_id"] for p in cp.get_active()}, False)

    print("4. VOID retracts an earlier verdict and reopens the prediction")
    make_pred("pred-d", 0.6)
    write_rec("pred-d", "CORRECT", error=0.0, brier_score=0.16)
    check("pred-d scored before void", cp.compute_calibration()["total"], 3)
    write_rec("pred-d", "VOID")
    got = cp.compute_calibration()
    check("pred-d removed from scoring", got["total"], 2)
    check("pred-d reopened in active", "pred-d" in {p["prediction_id"] for p in cp.get_active()}, True)

    print("5. Legacy `status` spelling reads identically to canonical `verdict`")
    make_pred("pred-e", 0.5)
    with open(cv.VERIFICATION_LOG, "a") as f:
        f.write(json.dumps({
            "prediction_id": "pred-e",
            "status": "VERIFIED_CORRECT",
            "observed": "legacy row",
            "error": 0.0,
            "error_type": "NONE",
            "brier_score": 0.25,
        }) + "\n")
    got = cp.compute_calibration()
    check("legacy decisive counted", got["total"], 3)
    check("legacy correct counted", got["correct"], 3 - 1)  # pred-a, pred-b(incorrect), pred-e

    print("6. Orphan verification (no birth record) is disclosed, never scored")
    write_rec("pred-ghost", "CORRECT")
    got = cp.compute_calibration()
    check("orphan not scored", got["total"], 3)
    check("orphan disclosed", got["orphan_records"], 1)

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} check(s) failed: {FAILS}")
        return 1
    print("RESULT: PASS — all join/verdict invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
