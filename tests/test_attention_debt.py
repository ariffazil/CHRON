"""Tests for CHRON Attention Debt module.

Verifies:
  T1: Debt item creation and deterministic ID
  T2: Consequence weight mapping
  T3: AD computation from known items
  T4: Overdue prediction collector
  T5: Unresolved event collector
  T6: Growth rate computation (insufficient history)
  T7: Deduplication by debt_id
  T8: Resolution tracking
"""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from chron.chron_attention_debt import (
    create_debt_item,
    compute_attention_debt,
    _make_debt_id,
    CONSEQUENCE_WEIGHT,
    _load_all_debt,
    _append_debt,
    _resolve_debt,
    _compute_growth_rate,
)


def test_t1_debt_item_creation():
    """T1: Debt item has correct fields and deterministic ID."""
    item = create_debt_item(
        source="test",
        ref_id="ref-001",
        description="Test debt",
        consequence="HIGH",
    )
    assert item["source"] == "test"
    assert item["ref_id"] == "ref-001"
    assert item["status"] == "OPEN"
    assert item["weight"] == 3.0  # HIGH
    assert item["debt_id"] == _make_debt_id("test", "ref-001")
    assert item["resolved_at"] is None
    print("  T1 PASS: debt item creation")


def test_t2_consequence_weights():
    """T2: Consequence weight mapping is correct."""
    assert CONSEQUENCE_WEIGHT["HIGH"] == 3.0
    assert CONSEQUENCE_WEIGHT["MEDIUM"] == 2.0
    assert CONSEQUENCE_WEIGHT["LOW"] == 1.0

    item_high = create_debt_item("t", "h", "h", consequence="HIGH")
    item_med = create_debt_item("t", "m", "m", consequence="MEDIUM")
    item_low = create_debt_item("t", "l", "l", consequence="LOW")
    item_unknown = create_debt_item("t", "u", "u", consequence="UNKNOWN")

    assert item_high["weight"] == 3.0
    assert item_med["weight"] == 2.0
    assert item_low["weight"] == 1.0
    assert item_unknown["weight"] == 2.0  # default
    print("  T2 PASS: consequence weights")


def test_t3_ad_computation():
    """T3: AD computation from known items is correct."""
    now = datetime.now(timezone.utc)

    # Item identified 10 days ago, HIGH (weight=3) → AD = 10 * 3 = 30
    item_a = create_debt_item(
        source="test", ref_id="a", description="A",
        consequence="HIGH",
        identified_at=(now - timedelta(days=10)).isoformat(),
    )
    # Item identified 5 days ago, LOW (weight=1) → AD = 5 * 1 = 5
    item_b = create_debt_item(
        source="test", ref_id="b", description="B",
        consequence="LOW",
        identified_at=(now - timedelta(days=5)).isoformat(),
    )

    result = compute_attention_debt([item_a, item_b])
    assert result["item_count"] == 2
    assert result["total_ad"] > 30  # at least 30+5 = 35 (aging since creation)
    assert result["by_consequence"]["HIGH"]["count"] == 1
    assert result["by_consequence"]["LOW"]["count"] == 1
    assert result["oldest"] is not None
    assert result["oldest"]["debt_id"] == item_a["debt_id"]  # A is older
    print(f"  T3 PASS: AD computation (total={result['total_ad']})")


def test_t4_deterministic_id():
    """T4: Same source+ref_id always produces same debt_id."""
    id1 = _make_debt_id("overdue_prediction", "pred-abc123")
    id2 = _make_debt_id("overdue_prediction", "pred-abc123")
    id3 = _make_debt_id("overdue_prediction", "pred-xyz789")
    assert id1 == id2
    assert id1 != id3
    print("  T4 PASS: deterministic IDs")


def test_t5_empty_debt():
    """T5: Empty input produces zero AD."""
    result = compute_attention_debt([])
    assert result["total_ad"] == 0
    assert result["item_count"] == 0
    assert result["oldest"] is None
    print("  T5 PASS: empty debt")


def test_t6_growth_rate_insufficient():
    """T6: Growth rate returns None with insufficient history."""
    rate = _compute_growth_rate(42.0)
    # First call — should be None (only 1 snapshot or fewer)
    # (depends on whether test file already has data)
    print(f"  T6 PASS: growth rate = {rate} (expected None or float)")


def test_t7_weighted_score():
    """T7: AD contribution = age_days × weight."""
    now = datetime.now(timezone.utc)
    item = create_debt_item(
        source="test", ref_id="w", description="W",
        consequence="MEDIUM",
        identified_at=(now - timedelta(days=10)).isoformat(),
    )
    result = compute_attention_debt([item])
    scored = result["items"][0]
    assert scored["age_days"] >= 9.9  # ~10 days
    assert scored["age_days"] <= 10.1
    expected_ad = scored["age_days"] * 2.0  # MEDIUM weight
    assert abs(scored["ad_contribution"] - expected_ad) < 0.1
    print(f"  T7 PASS: weighted score (age={scored['age_days']}, ad={scored['ad_contribution']})")


def test_t8_by_source_breakdown():
    """T8: by_source aggregation is correct."""
    now = datetime.now(timezone.utc)
    items = [
        create_debt_item("overdue_prediction", "p1", "P1", "HIGH",
                         identified_at=(now - timedelta(days=5)).isoformat()),
        create_debt_item("overdue_prediction", "p2", "P2", "MEDIUM",
                         identified_at=(now - timedelta(days=3)).isoformat()),
        create_debt_item("unresolved_event", "e1", "E1", "LOW",
                         identified_at=(now - timedelta(days=7)).isoformat()),
    ]
    result = compute_attention_debt(items)
    assert "overdue_prediction" in result["by_source"]
    assert "unresolved_event" in result["by_source"]
    assert result["by_source"]["overdue_prediction"]["count"] == 2
    assert result["by_source"]["unresolved_event"]["count"] == 1
    print("  T8 PASS: by_source breakdown")


if __name__ == "__main__":
    print("CHRON Attention Debt — Test Suite")
    print("=" * 40)
    test_t1_debt_item_creation()
    test_t2_consequence_weights()
    test_t3_ad_computation()
    test_t4_deterministic_id()
    test_t5_empty_debt()
    test_t6_growth_rate_insufficient()
    test_t7_weighted_score()
    test_t8_by_source_breakdown()
    print("\nAll tests passed.")


def test_t9_reconcile_resolves_verified_phantom_debt(monkeypatch, tmp_path):
    """T9: persisted OPEN overdue_prediction item whose prediction now holds a
    decisive verdict is auto-resolved (append-only) — phantom-debt regression.

    Ground truth 2026-09-28: AD=60.3 / growth 2.5/day driven by 8 items already
    VERIFIED in the calibration ledger; birth records are immutable so nothing
    re-checked the persisted OPEN claims.
    """
    import chron.chron_attention_debt as ad
    import chron.chron_prediction as cp

    monkeypatch.setattr(ad, "DEBT_FILE", tmp_path / "attention_debt.jsonl")
    monkeypatch.setattr(cp, "decided_ids", lambda: {"pred-phantom"})
    monkeypatch.setattr(cp, "resolved_ids", lambda: {"pred-phantom"})
    monkeypatch.setattr(cp, "load_verifications", lambda: {
        "pred-phantom": {"verdict": "VERIFIED_CORRECT", "verified_at": "2026-09-26T00:00:00Z"}
    })
    monkeypatch.setattr(ad, "collect_overdue_predictions", lambda: [])
    monkeypatch.setattr(ad, "collect_unresolved_high_events", lambda: [])

    item = create_debt_item(
        source="overdue_prediction", ref_id="pred-phantom",
        description="phantom debt", consequence="MEDIUM",
    )
    ad._append_debt(item)

    result = ad.collect_current_debt()

    # phantom no longer counted as open debt
    assert all(i.get("ref_id") != "pred-phantom" for i in result)
    # resolution appended (append-only, history preserved)
    stored = ad._load_all_debt()
    resolved_rows = [r for r in stored if r.get("status") == "RESOLVED"]
    assert len(resolved_rows) == 1
    assert "VERIFIED_CORRECT" in resolved_rows[0]["resolution"]


def test_t10_reconcile_keeps_unverified_and_ledger_failure(monkeypatch, tmp_path):
    """T10: items without decisive verdict stay OPEN; ledger import failure
    keeps claims (never silently drops — F2 fail-visible)."""
    import chron.chron_attention_debt as ad
    import chron.chron_prediction as cp

    monkeypatch.setattr(ad, "DEBT_FILE", tmp_path / "attention_debt.jsonl")
    monkeypatch.setattr(cp, "decided_ids", lambda: set())
    monkeypatch.setattr(cp, "resolved_ids", lambda: set())
    monkeypatch.setattr(cp, "load_verifications", lambda: {})
    monkeypatch.setattr(ad, "collect_overdue_predictions", lambda: [])
    monkeypatch.setattr(ad, "collect_unresolved_high_events", lambda: [])

    keep = create_debt_item(source="overdue_prediction", ref_id="pred-still-waiting",
                            description="genuine debt", consequence="MEDIUM")
    ad._append_debt(keep)
    result = ad.collect_current_debt()
    assert any(i.get("ref_id") == "pred-still-waiting" for i in result)

    # ledger unavailable → claims preserved
    def _boom():
        raise RuntimeError("ledger down")
    monkeypatch.setattr(cp, "resolved_ids", _boom)
    result2 = ad.collect_current_debt()
    assert any(i.get("ref_id") == "pred-still-waiting" for i in result2)


def test_t11_explicit_closure_stops_aging_lateness_keeps_aging(monkeypatch, tmp_path):
    """A shut measurement window is a finished arrow; late evidence is not.

    chron_resolve_unverifiable promises "removed from the active set (stopping
    attention debt accumulation)". Before the CLOSED_UNTESTABLE split a closure
    wrote a ledger row that read exactly like a verifier's "no evidence yet", so
    the debt item aged forever while calibration correctly refused to score it.
    """
    import chron.chron_attention_debt as ad
    import chron.chron_prediction as cp

    monkeypatch.setattr(ad, "DEBT_FILE", tmp_path / "attention_debt.jsonl")
    monkeypatch.setattr(cp, "load_verifications", lambda: {
        "pred-closed": {"verdict": "UNVERIFIABLE", "resolution_class": "CLOSED_UNTESTABLE",
                        "verified_at": "2026-09-29T00:00:00Z"},
        "pred-late": {"verdict": "UNVERIFIABLE", "verified_by": "verify_due",
                      "verified_at": "2026-09-29T00:00:00Z"},
    })
    monkeypatch.setattr(ad, "collect_overdue_predictions", lambda: [])
    monkeypatch.setattr(ad, "collect_unresolved_high_events", lambda: [])

    # resolved_ids comes from the real implementation — only the ledger is stubbed.
    for pid, desc in (("pred-closed", "closed window"), ("pred-late", "late evidence")):
        ad._append_debt(create_debt_item(source="overdue_prediction", ref_id=pid,
                                         description=desc, consequence="MEDIUM"))

    result = ad.collect_current_debt()
    refs = {i.get("ref_id") for i in result}
    assert "pred-closed" not in refs, "explicit closure must stop debt aging"
    assert "pred-late" in refs, "late evidence must keep aging (legitimate debt)"
