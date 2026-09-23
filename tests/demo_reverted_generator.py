#!/usr/bin/env python3
"""Deliberate-failure proof: revert the generator wiring, show the suite FAILS.

The claim under test is "the test suite fails on the OLD behaviour". That claim is
only meaningful if the old behaviour is actually reproduced and the suite is
actually seen to fail. This script does exactly that, on the real file:

  1. hash chron_prediction.py
  2. revert ONLY the generator wiring to its pre-2026-09-21 shape
     (emit the raw proposed confidence, drop the learning-hop audit trail)
  3. run test_calibration_loop.py as a subprocess and record the failure
  4. restore the file from the byte-identical .MINE backup in
     .backup-learning-loop-*/ and verify the sha256 matches step 1
  5. re-run the suite and record the pass

The restore runs in a finally block, and the sha256 is asserted afterwards, so the
canonical file cannot be left in the reverted state by a crash.

Run: python3 /root/chron/tests/demo_reverted_generator.py
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

CHRON = Path("/root/chron")
TARGET = CHRON / "chron_prediction.py"
SUITE = CHRON / "tests" / "test_calibration_loop.py"
ENV = {"PYTHONPATH": "/root", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def newest_mine_backup() -> Path:
    candidates = sorted(CHRON.glob(".backup-learning-loop-*/chron_prediction.py.MINE"))
    if not candidates:
        raise SystemExit("no .MINE backup found — cannot guarantee a restore")
    return candidates[-1]


def run_suite() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SUITE)], capture_output=True, text=True, env=ENV, timeout=300
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def revert_generator_wiring(source: str) -> str:
    """Reproduce the pre-2026-09-21 generator: raw proposal, no audit trail."""
    reverted, n_conf = re.subn(
        r"confidence=outcome\.calibrated,", "confidence=proposed,", source
    )
    if n_conf != 2:
        raise SystemExit(f"expected 2 confidence call sites, found {n_conf}")

    # Drop the audit kwargs that only exist because the generator consumes
    # calibration. Reproduces `pred` without a learning-hop trail.
    audit_block = re.compile(
        r"\n *# ── Learning-hop audit trail \(2026-09-21\) ──\n"
        r" *confidence_proposed=proposed,\n"
        r" *confidence_calibrated=outcome\.calibrated,\n"
        r" *calibration_quality=outcome\.quality,\n"
        r" *recalibration=outcome\.audit\(\),\n"
        r" *calibration_snapshot=digest,\n"
        r" *lessons_consumed=lesson_state,\n"
    )
    reverted, n_blocks = audit_block.subn("\n", reverted)
    if n_blocks != 2:
        raise SystemExit(f"expected 2 audit blocks, found {n_blocks}")
    return reverted


def main() -> int:
    original = TARGET.read_text()
    pre_hash = sha(TARGET)
    backup = newest_mine_backup()
    print(f"target        : {TARGET}")
    print(f"pre-revert sha: {pre_hash}")
    print(f"restore source: {backup}")

    try:
        TARGET.write_text(revert_generator_wiring(original))
        print(f"reverted sha  : {sha(TARGET)}  (generator wiring removed)")
        code, out = run_suite()
        Path("/root/chron/tests/evidence-reverted-suite.txt").write_text(out)
        print(f"\n--- suite on REVERTED wiring (exit {code}) ---")
        for line in out.splitlines():
            if "[FAIL]" in line or line.startswith("RESULT"):
                print("  " + line.strip()[:160])
        failed_checks = out.count("[FAIL]")
        passed_checks = out.count("[PASS]")
        print(f"  checks: {passed_checks} passed, {failed_checks} failed")
        print(f"  full output: /root/chron/tests/evidence-reverted-suite.txt")
    finally:
        TARGET.write_text(backup.read_text())
        post_hash = sha(TARGET)
        print(f"\nrestored sha  : {post_hash}")
        if post_hash != pre_hash:
            print("RESTORE FAILED — hash mismatch")
            return 2

    code_restored, out_restored = run_suite()
    print(f"\n--- suite on RESTORED wiring (exit {code_restored}) ---")
    print("  " + [l for l in out_restored.splitlines() if l.startswith("RESULT")][-1])

    ok = code != 0 and failed_checks > 0 and code_restored == 0
    print(
        "\nRESULT: "
        + (
            "PASS — suite fails on the old behaviour and passes on the new, "
            "and the file was restored byte-identically"
            if ok
            else "FAIL — the deliberate-failure demonstration did not hold"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
