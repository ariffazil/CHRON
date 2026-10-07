"""EXEC_PROBE evidence class — sandbox proof (musyawarah packet 2026-10-04, step 5).

Proves, without touching production data:
  1. POSITIVE: a due prediction bound to `aforge-exec:file_stat:<real file>`
     resolves VERIFIED_CORRECT via the read-only probe (error 0.0, EXEC_PROBE).
  2. NEGATIVE (required): a BOGUS probe spec (`aforge-exec:rm_rf:/`) DECLINES
     to UNVERIFIABLE — no verdict is manufactured, attempt logged, stays open.
  3. EFFECT_MISS: file_stat on a missing in-roots path resolves
     VERIFIED_INCORRECT (error 1.0, EFFECT_MISS) — the falsifier observed.
  4. service_list binding on a live unit resolves via the listing probe.

Live dependency: the A-FORGE MCP loopback surface (default :7072/mcp) must be
reachable — the probe is the system under test. Skips with a clear reason
if the lane is down.

Run: python3 /root/chron/tests/test_exec_probe.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Scratch episode store BEFORE chron imports (chron_store reads env at import).
_scratch = tempfile.mkdtemp(prefix="chron-execprobe-")
os.environ["CHRON_STORE_DIR"] = _scratch
sys.path.insert(0, "/root")

from chron import chron_prediction as cp  # noqa: E402
from chron import chron_verify as cv  # noqa: E402

# Sandbox every file surface this test can write.
_scratch_dir = Path(_scratch)
cp.PREDICTIONS_FILE = _scratch_dir / "predictions.jsonl"
cp.CALIBRATION_FILE = _scratch_dir / "calibration.json"
cv.VERIFICATION_LOG = _scratch_dir / "verification_log.jsonl"
cv.ATTEMPTS_LOG = _scratch_dir / "verification_attempts.jsonl"
# No live arifFlow receipts, no live calibration rewrite from the sandbox.
cv._emit_ariflow_verify = lambda *a, **k: None  # noqa: E731
cv.compute_calibration = lambda: {}  # noqa: E731
cv.save_calibration = lambda cal: None  # noqa: E731

PASS, FAIL = [], []


def _seed(probe_id: str, claim: str) -> str:
    pred = {
        "prediction_id": f"pred-execprobe-{len(PASS) + len(FAIL)}",
        "claim": claim,
        "expected_outcome": claim,
        "confidence": 0.5,
        "verify_at": "2020-01-01T00:00:00Z",  # long due
        "status": "ACTIVE",
        "binding": probe_id,
        "probe_id": probe_id,
        "source": "aforge_shadow",
        "source_id": "aforge-shadow-test",
        "falsification_rule": "declared effect absent at verify time",
        "success_rule": "declared effect present at verify time",
    }
    with open(cp.PREDICTIONS_FILE, "a") as f:
        f.write(json.dumps(pred) + "\n")
    return pred["prediction_id"]


def _run_case(name: str, pred_id: str, expect_status: str, expect_error, expect_class: str) -> None:
    summary = cv.run_verification(dry_run=False)
    row = next(r for r in summary["results"] if r["prediction_id"] == pred_id)
    ok = (
        row["status"] == expect_status
        and row["error"] == expect_error
        and row["error_class"] == expect_class
    )
    detail = f"{name}: status={row['status']} error={row['error']} class={row['error_class']}"
    if ok:
        PASS.append(detail)
    else:
        FAIL.append(
            f"{detail} — expected status={expect_status} error={expect_error} class={expect_class}"
        )


def main() -> int:
    # Lane reachable? Skip-die honestly if the SUT transport is down.
    if cv._exec_probe_call("forge_health_check", {}) is None:
        print("SKIP: A-FORGE MCP loopback surface unreachable — cannot test EXEC_PROBE.")
        return 0

    # POSITIVE seed file must live in a SHARED namespace path (a-forge units
    # run PrivateTmp=yes — host /tmp is invisible to the probe lane) AND
    # inside the F8 allowed roots (/root, /tmp, /data, /var/log).
    real_file = Path("/root/chron/artifacts/execprobe-effect-landed.txt")
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_text("landed")

    # 1. POSITIVE — file exists → CORRECT via probe
    p1 = _seed(f"aforge-exec:file_stat:{real_file}", f"file {real_file} exists")
    _run_case("POSITIVE file_stat", p1, "VERIFIED", 0.0, "EXEC_PROBE")

    # 2. NEGATIVE — bogus/non-allowlisted probe → DECLINE, never a verdict
    p2 = _seed("aforge-exec:rm_rf:/", "bogus probe must decline")
    _run_case("NEGATIVE bogus probe", p2, "UNVERIFIABLE", None, "AWAITING_EVIDENCE")

    # 3. EFFECT_MISS — missing in-roots path → INCORRECT (falsifier observed)
    p3 = _seed("aforge-exec:file_stat:/tmp/chron-execprobe-missing.txt", "file exists (it does not)")
    _run_case("EFFECT_MISS", p3, "VERIFIED", 1.0, "EFFECT_MISS")

    # 4. service_list on a live unit → CORRECT via listing probe
    p4 = _seed("aforge-exec:service_list:chron-mcp", "unit chron-mcp present in listing")
    _run_case("POSITIVE service_list", p4, "VERIFIED", 0.0, "EXEC_PROBE")

    # Canonical verification log holds ONLY decisive verdicts (no entry for the decline).
    verdicts = {}
    if cv.VERIFICATION_LOG.exists():
        for line in cv.VERIFICATION_LOG.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                verdicts[rec["prediction_id"]] = rec.get("verdict")
    if verdicts.get(p2) is not None:
        FAIL.append(f"DECLINE leaked a verdict into canonical log: {verdicts.get(p2)}")
    else:
        PASS.append("decline wrote NO canonical verdict (doctrine held)")

    for line in PASS:
        print(f"  PASS  {line}")
    for line in FAIL:
        print(f"  FAIL  {line}")
    print(f"\n{len(FAIL)} failures, {len(PASS)} passes")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
