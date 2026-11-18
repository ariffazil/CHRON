"""Causal-contract invariants — pins the 2026-11-16 learning-loop finding.

MEASURED BEFORE THE FIX (case pred-089ce72ac833):
  * verification_log.jsonl: VERIFIED_INCORRECT @ 2026-11-11T16:11Z
  * lessons.jsonl: lesson-assumption_error-9, status CANDIDATE, actionable=true,
    promotion_eligible=true, recurrence=9, text "Review assumption quality" —
    no failed claim, no root cause, no corrective action, no attribution
  * policy_candidates.jsonl: 16 physical rows, 1 unique candidate_id
  * chron_prediction.py: static policy_note naming a criterion DROPPED 2026-09-21

WHAT THIS FILE ASSERTS
  1. An aggregate lesson is MECHANISM_ABSENT and says so — disclosure, not failure.
  2. A lesson that genuinely carries a mechanism is MECHANISM_PRESENT.
  3. No behaviour change may be attributed to a lesson without the full chain.
  4. The promotion explanation is derived from the live gate and names no dead
     criterion (executable policy == explanatory policy).
  5. Candidate append is idempotent per candidate_id: rows != candidates.

Run: /opt/arifos/current/venv/bin/python3 -m pytest tests/test_causal_contract.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "chron" not in sys.modules and (ROOT.parent / "chron" / "__init__.py").exists():
    sys.path.insert(0, str(ROOT.parent))
else:
    sys.path.insert(0, str(ROOT))

from chron import chron_causal_contract as cc  # noqa: E402
from chron import chron_loop_close as lc  # noqa: E402
from chron.chron_prediction import policy_gate_note  # noqa: E402


# ── 1/2. lesson classification ──────────────────────────────────────────────


def test_aggregate_lesson_is_mechanism_absent():
    lesson = {
        "lesson_id": "lesson-assumption_error-9",
        "error_type": "ASSUMPTION_ERROR",
        "recurrence": 9,
        "mean_brier": 0.2982424833639355,
        "lesson": "Predictions whose declared assumptions failed are recurring "
        "(9 times). Review assumption quality ...",
        "status": "CANDIDATE",
        "actionable": True,
        "promotion_eligible": True,
    }
    ev = cc.evaluate_lesson(lesson)
    assert ev["status"] == cc.MECHANISM_ABSENT
    assert ev["causal_trustworthy"] is False
    assert ev["recurrence_is_not_evidence"] is True
    # The dangerous combination is disclosed in a field a reader can join on.
    assert ev["actionable_but_mechanism_absent"] is True
    assert set(cc.MECHANISM_FIELDS) <= set(ev["missing"])


def test_mechanism_bearing_lesson_is_present():
    lesson = {
        "lesson_id": "lesson-runtime-once-per-session-1",
        "error_type": "ASSUMPTION_ERROR",
        "recurrence": 1,
        "mean_brier": 0.25,
        "failed_claim": "auto_init guard executes once per session",
        "root_cause": {
            "component": "lib/reality-loop.ts",
            "mechanism": "once-per-session behaviour documented but not enforced",
            "evidence": ["reality-loop.ts:source_location", "ledger_query"],
        },
        "corrective_action": {
            "type": "CODE_CHANGE",
            "artifact": "AutoInitGate",
            "owner_lane": "CODE",
        },
    }
    ev = cc.evaluate_lesson(lesson)
    assert ev["status"] == cc.MECHANISM_PRESENT
    assert ev["causal_trustworthy"] is True
    assert ev["missing"] == []


def test_junk_is_not_a_lesson():
    assert cc.evaluate_lesson({})["status"] == cc.NOT_A_LESSON
    assert cc.evaluate_lesson(None)["status"] == cc.NOT_A_LESSON  # type: ignore[arg-type]


# ── 3. attribution barrier ──────────────────────────────────────────────────


def test_attribution_defaults_to_unknown():
    rec = cc.attribution_record()
    assert rec["behaviour_change_attribution"] == cc.ATTRIBUTION_UNKNOWN
    assert rec["learning_loop_contribution"] == cc.ATTRIBUTION_NOT_ESTABLISHED
    assert set(rec["chain_missing"]) == set(cc.ATTRIBUTION_CHAIN)


def test_attribution_requires_full_chain():
    partial = cc.attribution_record({"lesson_id": "l1", "policy_candidate_id": "p1"})
    assert partial["behaviour_change_attribution"] == cc.ATTRIBUTION_UNKNOWN
    assert "deployment_receipt" in partial["chain_missing"]

    full = {link: f"x-{link}" for link in cc.ATTRIBUTION_CHAIN}
    full["actual_change_lane"] = "CODE_LANE"
    rec = cc.attribution_record(full)
    assert rec["behaviour_change_attribution"] == "CODE_LANE"
    assert rec["learning_loop_contribution"] == "ESTABLISHED"
    assert rec["chain_missing"] == []


# ── 4. explanation derived from the live gate ───────────────────────────────


def test_policy_note_is_derived_and_names_no_dead_criterion():
    note = policy_gate_note()
    assert "external validation is not implemented" not in note
    assert "DROPPED" in note
    # The live gate's real criteria must appear.
    assert str(lc.CRITERION_RECURRENCE_MIN) in note
    # Dropped criterion is named as dropped, from the gate's own constant.
    assert lc.CRITERION_3_DROPPED in note


# ── 5. rows != candidates ───────────────────────────────────────────────────


def _row(lesson_id: str) -> dict:
    return {
        "lesson_id": lesson_id,
        "error_type": "RANGE_STALE_AT_MINT",
        "recurrence": 2,
        "criteria": {"measured_effect": {"evidence": {"share_before": 0.4}}},
    }


def test_policy_candidate_append_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        orig = lc.POLICY_CANDIDATES_FILE
        try:
            lc.POLICY_CANDIDATES_FILE = Path(td) / "policy_candidates.jsonl"
            first = lc._write_policy_candidates([_row("lesson-x-2")])
            second = lc._write_policy_candidates([_row("lesson-x-2")])
            third = lc._write_policy_candidates([_row("lesson-y-2")])

            assert (first, second, third) == (1, 0, 1)
            rows = [
                json.loads(l)
                for l in lc.POLICY_CANDIDATES_FILE.read_text().splitlines()
                if l.strip()
            ]
            assert len(rows) == 2
            assert lc.policy_candidates_unique_total() == 2
            # Every candidate carries attribution: promotion is not outcome.
            assert rows[0]["attribution"]["behaviour_change_attribution"] == "UNKNOWN"
        finally:
            lc.POLICY_CANDIDATES_FILE = orig


def test_report_counts_coverage():
    rows = [
        {"lesson_id": "l1", "error_type": "ASSUMPTION_ERROR", "actionable": True},
        {
            "lesson_id": "l2",
            "error_type": "ASSUMPTION_ERROR",
            "failed_claim": "c",
            "root_cause": {"component": "f", "mechanism": "m"},
            "corrective_action": {"type": "CODE_CHANGE"},
        },
    ]
    rep = cc.report(rows)
    assert rep["lessons_examined"] == 2
    assert rep["mechanism_present"] == 1
    assert rep["mechanism_absent"] == 1
    assert rep["actionable_without_mechanism"] == 1
    assert abs(rep["mechanism_coverage"] - 0.5) < 1e-9


# ── 6. every zero carries its denominator ───────────────────────────────────


def test_failure_rate_refuses_zero_exposure():
    r = cc.failure_rate(0, 0)
    assert r["state"] == "UNMEASURED_NO_EXPOSURE"
    assert r["rate"] is None


def test_failure_rate_carries_denominator():
    pre = cc.failure_rate(14, 133)
    assert pre["state"] == "MEASURED"
    assert abs(pre["rate"] - 14 / 133) < 1e-12
    post = cc.failure_rate(0, 53)
    assert post["state"] == "MEASURED"
    assert post["rate"] == 0.0
    assert post["opportunities"] == 53  # zero with a denominator, not a bare zero
