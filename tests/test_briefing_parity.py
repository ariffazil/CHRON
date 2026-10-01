"""CHRON briefing parity — the temporal state an agent inherits must be the
same temporal state the organ acts on.

Defect class proven 2026-10-01: chron_briefing.py derived `active` from the
birth store's ``status`` field and compared ``verify_at`` to ``now`` as
*strings*. Both are wrong in the same direction:

  1. The birth record always reads ``status: ACTIVE`` — that is the immutability
     invariant, not the arrow's state. Counting it as open re-reported ten
     already-verified predictions as "due" into carry_forward.json, which every
     agent reads at boot (arifos://carry-forward).
  2. ``verify_at`` is stored offset-spelled (``2026-09-30T23:00:00+08:00``) and
     ``now`` was formatted ``...Z``. Lexicographic comparison of two different
     spellings misjudges any horizon inside the UTC-offset band: that instant is
     15:00Z, i.e. already due at 16:00Z, but "23:" > "16:" says not due.

The canonical join already exists in chron_prediction.get_active()/get_due() and
is what chron_store_stats() reports. The briefing must read that arbiter, not
re-derive time alongside it.

Run: PYTHONPATH=/root /opt/arifos/current/venv/bin/python3 -m pytest /root/chron/tests -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, "/root")

from chron import chron_briefing as cb  # noqa: E402
from chron import chron_prediction as cp  # noqa: E402

# Frozen so the offset-vs-Z comparison bug is reachable deterministically:
# 16:00Z is strictly AFTER 23:00+08:00 (=15:00Z) but sorts BEFORE it as text.
NOW = datetime(2026, 9, 30, 16, 0, 0, tzinfo=timezone.utc)


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _pred(pid: str, verify_at: str, status: str = "ACTIVE") -> dict:
    return {
        "prediction_id": pid,
        "claim": f"scratch claim {pid}",
        "expected_outcome": "scratch",
        "confidence": 0.7,
        "verify_at": verify_at,
        "status": status,
        "created_at": "2026-09-18T00:00:00Z",
    }


BORN_ACTIVE_ALREADY_VERIFIED = "pred-closed-late"
DUE_BY_INSTANT_ONLY = "pred-offset-due"
STILL_OPEN_FUTURE = "pred-future"


@pytest.fixture()
def scratch_store(tmp_path, monkeypatch):
    """A scratch CHRON store. Production paths are never opened."""
    preds = [
        # Arrow already closed by a decisive verdict; birth record still ACTIVE.
        _pred(BORN_ACTIVE_ALREADY_VERIFIED, "2026-09-20T00:00:00+08:00"),
        # Due in instant terms (15:00Z < 16:00Z), NOT due under string compare.
        _pred(DUE_BY_INSTANT_ONLY, "2026-09-30T23:00:00+08:00"),
        # Genuinely not yet due — both semantics agree.
        _pred(STILL_OPEN_FUTURE, "2026-10-09T23:59:59+08:00"),
    ]
    pred_file = tmp_path / "predictions.jsonl"
    pred_file.write_text("\n".join(json.dumps(p) for p in preds) + "\n")

    verif_file = tmp_path / "verification_log.jsonl"
    verif_file.write_text(json.dumps({
        "verification_id": "verif-scratch-closed-late",
        "prediction_id": BORN_ACTIVE_ALREADY_VERIFIED,
        "verdict": "VERIFIED_CORRECT",
        "observed_outcome": "scratch outcome",
        "error_type": "NONE",
        "brier_score": 0.09,
        "verified_at": "2026-09-21T00:00:00Z",
    }) + "\n")

    (tmp_path / "lessons.jsonl").write_text("")
    (tmp_path / "loop_log.jsonl").write_text("")
    (tmp_path / "episodes.jsonl").write_text("")

    # Briefing side
    monkeypatch.setattr(cb, "CHRON_DATA", tmp_path)
    monkeypatch.setattr(cb, "HERMES_CHRON", tmp_path / "hermes-nonexistent")
    monkeypatch.setattr(cb, "CARRY_FORWARD", tmp_path / "carry_forward.json")
    monkeypatch.setattr(cb, "datetime", _FrozenDT)
    # Canonical-join side
    monkeypatch.setattr(cp, "PREDICTIONS_FILE", pred_file)
    monkeypatch.setattr(cp, "VERIFICATION_LOG", verif_file)
    monkeypatch.setattr(cp, "datetime", _FrozenDT)
    # resolution_states() reads the attempts log directly from a module constant
    monkeypatch.setattr(cp, "VERIFICATION_ATTEMPTS", tmp_path / "verification_attempts.jsonl")
    return tmp_path


def _ids(rows):
    return sorted(r.get("prediction_id") or r.get("id") for r in rows)


def test_briefing_agrees_with_canonical_join(scratch_store):
    """The briefing's due set must be exactly what the arbiter says is due."""
    briefing = cb.generate_briefing()
    assert briefing["predictions"]["due_count"] == len(cp.get_due())
    assert _ids(briefing["predictions"]["due"]) == _ids(cp.get_due())
    assert briefing["predictions"]["active_count"] == len(cp.get_active())


def test_closed_arrow_is_not_reported_as_owed(scratch_store):
    """A prediction with a decisive verdict must leave both the active and due sets,
    even though its immutable birth record still reads ACTIVE."""
    briefing = cb.generate_briefing()
    due_ids = _ids(briefing["predictions"]["due"])
    active_ids = _ids(briefing["predictions"]["upcoming"]) + due_ids
    assert BORN_ACTIVE_ALREADY_VERIFIED not in due_ids, "verified prediction re-reported as due"
    assert BORN_ACTIVE_ALREADY_VERIFIED not in active_ids, "closed arrow still counted open"


def test_due_uses_instants_not_string_spelling(scratch_store):
    """23:00+08:00 is 15:00Z, so at 16:00Z it IS due — regardless of spelling."""
    briefing = cb.generate_briefing()
    assert briefing["predictions"]["due_count"] == 1
    assert _ids(briefing["predictions"]["due"]) == [DUE_BY_INSTANT_ONLY]


def test_briefing_has_no_private_clock(scratch_store, monkeypatch):
    """Structural law: one owner, one path. If the arbiter says nothing is due,
    the briefing must report nothing due. A briefing that computes time on its own
    will keep reporting a number here and fail this check."""
    monkeypatch.setattr(cp, "get_due", lambda: [])
    monkeypatch.setattr(cp, "get_active", lambda: [])
    briefing = cb.generate_briefing()
    assert briefing["predictions"]["due_count"] == 0
    assert briefing["predictions"]["active_count"] == 0


def test_legacy_mirror_counted_on_its_real_field(scratch_store, monkeypatch):
    """The Hermes mirror spells temporal state ``state``, not ``status``. Reading the
    wrong field reports 0 while four arrows are genuinely open there — a silent zero,
    which is the same defect class this patch removes in the canonical path.
    Mirror rows must also stay OUT of the canonical census."""
    mirror_dir = scratch_store / "hermes_mirror"
    mirror_dir.mkdir()
    (mirror_dir / "predictions.json").write_text(json.dumps({
        "$schema": "chron_predictions_v0",
        "predictions": [
            {"prediction_id": "legacy-open", "state": "ACTIVE"},
            {"prediction_id": "legacy-closed", "state": "SUPERSEDED", "superseded_by": "x"},
            {"prediction_id": "legacy-fieldless"},
            {"prediction_id": BORN_ACTIVE_ALREADY_VERIFIED, "state": "ACTIVE"},  # canonical twin
        ],
    }))
    monkeypatch.setattr(cb, "HERMES_CHRON", mirror_dir)
    briefing = cb.generate_briefing()
    assert briefing["predictions"]["legacy_store_active"] == 1
    # beside the census, never merged into it
    assert briefing["predictions"]["active_count"] == len(cp.get_active())

