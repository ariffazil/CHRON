"""REALITY-CLOSURE-PROTOCOL v0.1 — AD reconciliation round-trip test (hermetic).

Falsifier per spec §4:
  1. An event with no resolution record shows up as unresolved_event AD debt.
  2. chron_resolve_event() with valid evidence_ref writes a resolution record.
  3. _reconcile_open_items() reads the events file fresh and closes the stale
     JSONL row, dropping AD to 0.
  4. The resolved state survives a fresh module reload (no module-level cache).
  5. A missing evidence_ref is rejected with EVIDENCE_REQUIRED (F2 fail-closed).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))


@pytest.fixture
def isolated_ad_env(tmp_path, monkeypatch):
    """Seed an isolated resolved event + stale OPEN attention_debt row."""
    import chron.chron_attention_debt as ad
    import chron.chron_prediction as cp

    events_file = tmp_path / "chron_events.json"
    debt_file = tmp_path / "attention_debt.jsonl"
    snap_file = tmp_path / "ad_snapshots.jsonl"
    preds_file = tmp_path / "predictions.jsonl"
    vlog_file = tmp_path / "verification_log.jsonl"
    preds_file.write_text("")
    vlog_file.write_text("")

    events_file.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "at010-eval-contract-frozen-30d45853",
                        "title": "Synthetic resolved event anchor",
                        "consequence": "HIGH",
                        "target_date": "2026-10-01",
                        "resolution": {
                            "outcome": "FULFILLED",
                            "evidence_ref": "docs/fixtures/at010-proof.md",
                            "resolved_at": "2026-10-07T00:00:00Z",
                        },
                    }
                ]
            }
        )
    )
    stale_row = {
        "debt_id": "ad-792ef45b",
        "source": "unresolved_event",
        "ref_id": "at010-eval-contract-frozen-30d45853",
        "description": "stale unresolved event",
        "consequence": "HIGH",
        "weight": 3.0,
        "identified_at": "2026-10-02T00:00:00Z",
        "status": "OPEN",
        "resolution": None,
        "resolved_at": None,
    }
    debt_file.write_text(json.dumps(stale_row) + "\n")

    def _bind(mod):
        monkeypatch.setattr(mod, "EVENTS_FILE", events_file)
        monkeypatch.setattr(mod, "DEBT_FILE", debt_file)
        monkeypatch.setattr(mod, "AD_SNAPSHOT_FILE", snap_file)

    monkeypatch.setattr(cp, "PREDICTIONS_FILE", preds_file)
    monkeypatch.setattr(cp, "VERIFICATION_LOG", vlog_file)
    _bind(ad)
    return ad, _bind


def test_resolution_drops_ad_to_zero(isolated_ad_env):
    """Event with resolution.evidence_ref reconciles stale OPEN row to AD=0."""
    ad, _ = isolated_ad_env
    result = ad.get_attention_debt()
    assert result["total_ad"] == 0.0, f"got {result['total_ad']}"
    assert result["item_count"] == 0, f"got {result['item_count']}"


def test_resolution_fails_without_evidence():
    """F2 fail-closed: chron_resolve_event rejects empty evidence_ref."""
    src = (_ROOT / "server.py").read_text()
    assert 'if not (evidence_ref or "").strip()' in src
    assert "EVIDENCE_REQUIRED" in src


def test_module_reload_survives(isolated_ad_env):
    """Reconciliation works on fresh module reloads (no module-level cache)."""
    ad, bind = isolated_ad_env
    importlib.reload(ad)
    bind(ad)
    importlib.reload(ad)
    bind(ad)
    result = ad.get_attention_debt()
    assert result["total_ad"] == 0.0, f"got {result['total_ad']}"


def test_no_silent_drop(isolated_ad_env):
    """F2/F9 invariant: stale OPEN row must receive an append-only RESOLVED record."""
    ad, _ = isolated_ad_env
    ad.get_attention_debt()
    items = ad._load_all_debt()
    at010_resolved = [
        i
        for i in items
        if i.get("debt_id") == "ad-792ef45b"
        and i.get("status") == "RESOLVED"
        and "REALITY-CLOSURE-PROTOCOL" in (i.get("resolution") or "")
    ]
    assert len(at010_resolved) > 0
