#!/usr/bin/env python3
"""Unit tests — QCOS Phase 0 admissibility gate on save_prediction.
Uses temp store; touches NO live ledger."""

import sys, json, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chron_prediction as cp


def run():
    tmp = Path(tempfile.mkdtemp()) / "preds.jsonl"
    orig = cp.PREDICTIONS_FILE
    cp.PREDICTIONS_FILE = tmp
    try:
        good = {
            "prediction_id": "pred-testgood0001",
            "claim": "x will exceed y by date z",
            "expected_outcome": ">y",
            "confidence": 0.6,
            "verify_at": "2026-12-31T23:59:59+08:00",
            "falsifier": "x at or below y",
            "verifier_method": "rerun query",
        }
        bad = {
            "prediction_id": "pred-testbad00001",
            "claim": "x will exceed y by date z",
            "expected_outcome": ">y",
            "confidence": 0.6,
            "verify_at": "2026-12-31T23:59:59+08:00",
            "falsifier": "",
            "verifier_method": "",
        }
        cp.save_prediction(good)
        cp.save_prediction(bad)
        rows = [json.loads(l) for l in tmp.read_text().splitlines()]
        assert (
            rows[0]["admissible"] is True and "inadmissible_reasons" not in rows[0]
        ), rows[0]
        assert rows[1]["admissible"] is False, rows[1]
        assert rows[1]["syndrome"] == "EVIDENCE_GAP", rows[1]
        assert set(rows[1]["inadmissible_reasons"]) == {
            "falsifier",
            "verification_method",
        }, rows[1]
        print(
            "TEST PASS: good stamped admissible=True; bad stamped admissible=False + EVIDENCE_GAP + reasons"
        )
        print("rows written:", len(rows), "| temp store:", tmp)
        return 0
    finally:
        cp.PREDICTIONS_FILE = orig


if __name__ == "__main__":
    sys.exit(run())
