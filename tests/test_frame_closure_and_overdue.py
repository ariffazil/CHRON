"""Tests for CHRON overdue_days recomputation/sorting and FRAME-witness closure."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from chron import chron_attention_debt, chron_frame, chron_prediction


def test_overdue_days_recomputed_and_sorted_by_due_date():
    now = datetime.now(timezone.utc)
    old_due = (now - timedelta(days=18)).isoformat().replace("+00:00", "Z")
    new_due = (now - timedelta(hours=6)).isoformat().replace("+00:00", "Z")

    items = [
        {
            "debt_id": "ad-new",
            "source": "overdue_prediction",
            "ref_id": "pred-new",
            "description": "Prediction overdue by 0d: newly due item",
            "consequence": "MEDIUM",
            "weight": 2.0,
            "status": "OPEN",
            "identified_at": new_due,
            "overdue_days": 0.0,
            "claim": "newly due item",
        },
        {
            "debt_id": "ad-old",
            "source": "overdue_prediction",
            "ref_id": "pred-old",
            "description": "Prediction overdue by 0d: Nov 2 item",
            "consequence": "MEDIUM",
            "weight": 2.0,
            "status": "OPEN",
            "identified_at": old_due,
            "overdue_days": 0.0,
            "claim": "Nov 2 item",
        },
    ]
    res = chron_attention_debt.compute_attention_debt(items=items)
    scored = res["items"]
    assert len(scored) == 2
    assert scored[0]["debt_id"] == "ad-old"
    assert scored[0]["overdue_days"] >= 17.9
    assert "18d:" in scored[0]["description"]
    assert scored[1]["debt_id"] == "ad-new"
    assert scored[1]["overdue_days"] < 1.0


def test_close_prediction_with_frame_once_and_awaiting_on_unverified(tmp_path: Path, monkeypatch):
    log_file = tmp_path / "verification_log.jsonl"
    monkeypatch.setattr(chron_prediction, "VERIFICATION_LOG", log_file)
    monkeypatch.setattr(chron_prediction, "PREDICTIONS_FILE", tmp_path / "predictions.jsonl")

    pred = {
        "prediction_id": "pred-stg-001",
        "claim": "Staging target updated under permit",
        "expected_outcome": "SHA-256 changes and matches receipt",
        "confidence": 0.85,
        "assumptions": [],
        "evidence_snapshot": [],
        "source_refs": [],
        "world_state_hash": None,
        "model": "chron_default",
    }

    # Unverified FRAME observation keeps prediction open
    r_wait = chron_frame.close_prediction_with_frame(
        pred,
        {"witness_verdict": "UNVERIFIED", "reason": "target unreadable"},
        log_path=log_file,
    )
    assert r_wait["status"] == "AWAITING_EVIDENCE"
    assert "target unreadable" in r_wait["reason"]
    assert not log_file.exists()

    # Valid FRAME WITNESS_MATCH closes once
    r_closed = chron_frame.close_prediction_with_frame(
        pred,
        {
            "action_id": "act-stg-1",
            "witness_verdict": "WITNESS_MATCH",
            "kernel_verdict": "SEAL",
            "executor_receipt_id": "rcpt-stg-1",
            "reason": "target mutation observed and matched with kernel verdict + executor receipt",
            "after": {"sha256": "abc123"},
        },
        log_path=log_file,
    )
    assert r_closed["status"] == "CLOSED"
    assert r_closed["verdict"] == "VERIFIED_CORRECT"
    assert len(log_file.read_text().splitlines()) == 1

    # Second call is idempotent -> ALREADY_CLOSED, no duplicate row
    r_dup = chron_frame.close_prediction_with_frame(
        pred,
        {"witness_verdict": "WITNESS_MATCH", "reason": "duplicate"},
        log_path=log_file,
    )
    assert r_dup["status"] == "ALREADY_CLOSED"
    assert len(log_file.read_text().splitlines()) == 1
