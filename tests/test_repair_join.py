"""CHRON repair proof — join correctness on a scratch store.

Proves, without touching production data:
  1. A decisive CORRECT verdict produces accuracy 1.0
  2. An INCORRECT verdict produces accuracy 0.0 and is scored
  3. An UNVERIFIABLE record does NOT resolve the prediction and is NOT scored
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
    check("mean_brier", got["mean_brier"], 0.16)

    print("3. UNVERIFIABLE does NOT resolve and is NOT scored")
    make_pred("pred-c", 0.7)
    write_rec("pred-c", "UNVERIFIABLE", error=None, brier_score=None)
    got = cp.compute_calibration()
    check("scored total unchanged", got["total"], 2)
    check("unverifiable disclosed", got["unverifiable"], 1)
    check("accuracy unchanged", got["accuracy"], 0.5)
    active_ids = {p["prediction_id"] for p in cp.get_active()}
    check("pred-c still active", "pred-c" in active_ids, True)
    check("pred-a resolved (not active)", "pred-a" in active_ids, False)

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
