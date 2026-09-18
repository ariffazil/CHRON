#!/usr/bin/env python3
"""CHRON Verification Cron — daily entry point.

2026-09-18 (loop-closure repair, V-series): this file used to be a SECOND,
divergent implementation of verification. It carried its own outcome logic,
its own verification-log writer, and — worst of all — it REWROTE
`predictions.jsonl`, mutating records the whole store is built to keep
immutable. Three consequences were measured:

  * V1  birth records were overwritten, so the belief snapshot that produced a
        prediction did not survive verification. Calibration could no longer be
        audited against what was actually believed.
  * V2  a third schema of row reached verification_log.jsonl, mixing with the
        canonical records and making the join ambiguous.
  * V3  outcomes were decided by asserting that a passed calendar date meant the
        claim held ("assuming occurred per date commitment"), which minted
        CORRECT verdicts with no evidence at all.

There is now exactly ONE verification implementation: `chron.chron_verify`.
This module is a scheduler entry point that calls it and prints a human-readable
report. It gathers no evidence, decides no verdict, writes no prediction record.

Evidence: /root/chron/REPAIR-RECEIPT-2026-09-18.md
Rollback: /root/chron/.backup-chron-repair-20260918T152417Z/chron_cron_verify.py

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chron.chron_prediction import compute_calibration, get_active, save_calibration
from chron.chron_verify import run_verification

MYT = timezone(timedelta(hours=8))


def main() -> int:
    now = datetime.now(MYT)
    print(f"CHRON Verification Cron — {now.strftime('%Y-%m-%d %H:%M MYT')}")

    active = get_active()
    print(f"  Unresolved predictions: {len(active)}")

    result = run_verification(dry_run=False)

    due = result.get("due", 0)
    if due == 0:
        print("  Due now: 0 — nothing has reached verify_at. No verdict written.")
    else:
        print(
            f"  Due now: {due}"
            f"  correct={result.get('verified_correct', 0)}"
            f"  incorrect={result.get('verified_incorrect', 0)}"
            f"  unverifiable={result.get('unverifiable', 0)}"
        )
        for r in result.get("results", []):
            pid = str(r.get("prediction_id"))[:20]
            print(f"    [{pid}] {r.get('status')}  err={r.get('error')}")
            print(f"      observed: {str(r.get('observed'))[:110]}")

    calibration = compute_calibration()
    save_calibration(calibration)
    print(
        "  Calibration: "
        f"decisive={calibration.get('total')} "
        f"accuracy={calibration.get('accuracy')} "
        f"mean_brier={calibration.get('mean_brier')} "
        f"unverifiable={calibration.get('unverifiable')}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
