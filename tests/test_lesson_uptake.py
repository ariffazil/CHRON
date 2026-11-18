#!/usr/bin/env python3
"""Adversarial validation for lesson_uptake.py (p1-lesson-uptake, F13 T1).

Demonstrations (1-8) map 1:1 to the ARIF authorisation of 2026-11-02.
All fixtures are synthetic; no real human data and no live store is written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import lesson_uptake as lu  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

VALID_LESSON = {
    "lesson_id": "lesson-fixture-valid-2",
    "fingerprint": "fp-fixture-valid",
    "recurrence": 2,
    "status": "CANDIDATE",
    "created_at": "2026-11-01T12:00:00Z",
    "prediction_ids": ["pred-fixture-1"],
    "error_type": "RANGE_STALE_AT_MINT",
    "lesson": "Range claims need a spot-check at mint (synthetic fixture).",
}
SUPERSEDED_TWIN = dict(
    VALID_LESSON, status="SUPERSEDED", created_at="2026-10-01T12:00:00Z"
)
NO_PROVENANCE = {
    "lesson_id": "lesson-fixture-noprov",
    "fingerprint": "fp-noprov",
    "recurrence": 1,
    "status": "CANDIDATE",
    "created_at": "2026-11-01T12:00:00Z",
    "prediction_ids": ["pred-does-not-exist"],
    "lesson": "No provenance anywhere (synthetic fixture).",
}
PREDICTIONS = [
    {
        "prediction_id": "pred-fixture-1",
        "created_at": "2026-11-02T00:00:00Z",
        "claim": "in-scope WITH spot-check",
        "threshold_low": 1,
        "threshold_high": 2,
        "evidence_snapshot": [{"kind": "OBSERVATION"}],
    },
    {
        "prediction_id": "pred-fixture-2",
        "created_at": "2026-11-02T00:00:00Z",
        "claim": "in-scope WITHOUT spot-check",
        "threshold_low": 3,
        "threshold_high": 4,
    },
    {
        "prediction_id": "pred-fixture-3",
        "created_at": "2026-09-18T00:00:00Z",
        "claim": "pre-since spotless legacy",
        "threshold_low": 5,
        "threshold_high": 6,
    },
]


@pytest.fixture()
def env(tmp_path: Path):
    lessons = tmp_path / "lessons.jsonl"
    preds = tmp_path / "predictions.jsonl"
    bindings = tmp_path / "bindings.jsonl"
    executions = tmp_path / "executions.jsonl"
    lessons.write_text(
        "\n".join(json.dumps(r) for r in (SUPERSEDED_TWIN, VALID_LESSON, NO_PROVENANCE))
        + "\n"
    )
    preds.write_text("\n".join(json.dumps(r) for r in PREDICTIONS) + "\n")
    return {
        "tmp": tmp_path,
        "lessons": lessons,
        "preds": preds,
        "bindings": bindings,
        "executions": executions,
    }


def _bind(env, lesson_id="lesson-fixture-valid-2", extra=None, capsys=None):
    argv = [
        "bind",
        "--lesson-id",
        lesson_id,
        "--test-id",
        "AT-fixture-range-snapshot",
        "--criterion",
        "in-scope range claims carry a spot-check",
        "--since",
        "2026-11-01",
        "--lessons",
        str(env["lessons"]),
        "--predictions",
        str(env["preds"]),
        "--bindings",
        str(env["bindings"]),
    ] + (extra or [])
    return lu.main(argv)


def _run(env, capsys=None, extra=None):
    argv = [
        "run",
        "--lessons",
        str(env["lessons"]),
        "--predictions",
        str(env["preds"]),
        "--bindings",
        str(env["bindings"]),
        "--executions",
        str(env["executions"]),
    ] + (extra or [])
    return lu.main(argv)


# ── 1. a valid recurrence lesson produces ONE acceptance-test binding ────────


def test_1_valid_recurrence_lesson_binds_once_with_all_fields(env, capsys):
    assert _bind(env) == lu.EXIT_OK
    rows = [json.loads(l) for l in env["bindings"].read_text().splitlines()]
    assert len(rows) == 1
    b = rows[0]
    # every field the authorisation requires on the binding
    for key in (
        "lesson_id",
        "provenance",
        "subject_under_test",
        "acceptance_criterion",
        "test_id",
        "test_command",
        "created_at",
    ):
        assert key in b, f"binding missing {key}"
    assert b["lesson_recurrence"] == 2
    assert b["provenance"]["resolved_prediction_ids"] == ["pred-fixture-1"]
    # the binding must NOT carry an acceptance verdict of its own
    assert "human_acceptance" not in b and "governance_verdict" not in b


# ── 2. a malformed lesson is rejected ────────────────────────────────────────


def test_2_unknown_lesson_id_rejected_no_binding(env):
    rc = _bind(env, lesson_id="lesson-not-in-file")
    assert rc == lu.EXIT_REJECTED
    assert not env["bindings"].exists()


# ── 3. duplicate lesson does not create duplicate tests ──────────────────────


def test_3_duplicate_bind_is_idempotent_and_supersede_twin_collapses(env):
    assert _bind(env) == lu.EXIT_OK
    assert _bind(env) == lu.EXIT_OK  # second call: no new binding
    rows = [json.loads(l) for l in env["bindings"].read_text().splitlines()]
    assert len(rows) == 1  # one lesson_id -> one test
    # the SUPERSEDED twin row for the same lesson_id must not produce a second binding
    assert rows[0]["lesson_created_at"] == VALID_LESSON["created_at"]


# ── 4. an unsupported lesson remains UNKNOWN ─────────────────────────────────


def test_4_unsupported_lesson_is_unknown_and_records_nothing(env):
    rc = _bind(env, lesson_id="lesson-fixture-noprov")
    assert rc == lu.EXIT_UNKNOWN
    assert not env["bindings"].exists()


def test_4b_non_recurrence_lesson_is_unknown(env):
    env["lessons"].write_text(
        json.dumps(dict(VALID_LESSON, lesson_id="lesson-norec", recurrence=None)) + "\n"
    )
    assert _bind(env, lesson_id="lesson-norec") == lu.EXIT_UNKNOWN
    assert not env["bindings"].exists()


# ── 5. a passing command is not outcome acceptance ───────────────────────────


def test_5_passing_command_is_never_human_acceptance(env):
    assert (
        _bind(env, extra=["--command", f"{sys.executable} -c print(1)"]) == lu.EXIT_OK
    )
    assert _run(env) == lu.EXIT_OK
    ex = [json.loads(l) for l in env["executions"].read_text().splitlines()]
    assert len(ex) == 1
    e = ex[0]
    assert e["command_success"] is True
    assert e["governance_verdict"] == "TEST_PASSED_AWAITING_HUMAN"
    assert e["human_acceptance"] is None  # sovereign field, never derived
    assert e["lesson_mutated"] is False
    # the source lesson file is byte-identical: no auto-close, ever
    before = env["lessons"].read_bytes()
    _run(env)
    assert env["lessons"].read_bytes() == before


# ── 6. a failed acceptance test is visible and does not self-close ───────────


def test_6_failing_check_is_recorded_and_lesson_survives(env):
    failing_cmd = (
        f"{sys.executable} {REPO / 'lesson_uptake.py'} check-range-snapshot "
        f"--since 2026-11-01 --predictions {env['preds']} --lessons {env['lessons']}"
    )
    assert _bind(env, extra=["--command", failing_cmd]) == lu.EXIT_OK
    assert _run(env) == lu.EXIT_OK
    e = json.loads(env["executions"].read_text().splitlines()[-1])
    assert e["command_success"] is False
    assert e["governance_verdict"] == "TEST_FAILED_REVIEW"
    assert e["human_acceptance"] is None
    # lesson still present, unmodified
    lessons = [json.loads(l) for l in env["lessons"].read_text().splitlines()]
    assert any(r["lesson_id"] == "lesson-fixture-valid-2" for r in lessons)


# ── 7. re-running the mechanism is idempotent ────────────────────────────────


def test_7_rerun_is_idempotent(env):
    assert _bind(env) == lu.EXIT_OK
    assert _bind(env) == lu.EXIT_OK
    _run(env)
    _run(env)
    bindings = env["bindings"].read_text().splitlines()
    executions = env["executions"].read_text().splitlines()
    assert len(bindings) == 1  # state does not grow
    assert len(executions) == 2  # events do, one per run, by design
    ids = {json.loads(l)["binding_id"] for l in bindings}
    assert ids == {json.loads(l)["binding_id"] for l in executions}


# ── 8. existing gates / stores remain unchanged ──────────────────────────────


def test_8_mechanism_writes_only_its_own_registries(env):
    tracked = {p: p.stat().st_mtime_ns for p in env["tmp"].iterdir()}
    assert (
        _bind(env, extra=["--command", f"{sys.executable} -c print(1)"]) == lu.EXIT_OK
    )
    assert _run(env) == lu.EXIT_OK
    after = {p: p.stat().st_mtime_ns for p in env["tmp"].iterdir()}
    changed = {
        p.name
        for p in env["tmp"].iterdir()
        if p.name not in {x.name for x in tracked} or after[p] != tracked.get(p)
    }
    assert changed <= {"bindings.jsonl", "executions.jsonl"}, (
        f"unexpected writes: {changed}"
    )
    # lesson + prediction stores untouched
    assert after[env["lessons"]] == tracked[env["lessons"]]
    assert after[env["preds"]] == tracked[env["preds"]]


# ── acceptance-test surface itself ───────────────────────────────────────────


def test_check_range_snapshot_fails_on_violation_and_flags_backlog(env, capsys):
    rc = lu.main(
        [
            "check-range-snapshot",
            "--since",
            "2026-11-01",
            "--predictions",
            str(env["preds"]),
            "--lessons",
            str(env["lessons"]),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == lu.EXIT_ERR  # fixture has an in-scope spotless range claim
    assert out["result"] == "FAIL"
    assert out["violations"][0]["prediction_id"] == "pred-fixture-2"
    assert out["pre_since_backlog_spotless"] == [
        "pred-fixture-3"
    ]  # legacy visible, not failed
    # P1 correction (ARIF 2026-11-02): sample size + five-way acceptance semantics
    assert out["eligible_observation_count"] == 2  # both in-scope range claims
    assert out["uptake_observation_count"] == 1  # only the spot-checked one
    sem = out["acceptance_semantics"]
    assert sem["live_evaluation_coverage"]["eligible_observation_count"] == 2
    assert sem["observed_lesson_uptake"]["status"] == "PARTIAL_OR_VIOLATING"
    assert sem["human_acceptance"]["value"] is None
    assert sem["command_execution_success"]["status"] == "PER_EXECUTION_FIELD"


def test_1b_zero_sample_exposes_zero_eligible_never_uptake(env, capsys, tmp_path):
    """eligible_observation_count=0 must read as UNDETERMINED, never as uptake."""
    only_legacy = tmp_path / "preds_zero.jsonl"
    only_legacy.write_text(json.dumps(PREDICTIONS[2]) + "\n")  # pre-since row only
    rc = lu.main(
        [
            "check-range-snapshot",
            "--since",
            "2026-11-01",
            "--predictions",
            str(only_legacy),
            "--lessons",
            str(env["lessons"]),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == lu.EXIT_OK
    assert out["eligible_observation_count"] == 0
    assert out["uptake_observation_count"] == 0
    sem = out["acceptance_semantics"]
    assert sem["live_evaluation_coverage"]["status"] == "ZERO_ELIGIBLE_OBSERVATIONS"
    assert (
        sem["observed_lesson_uptake"]["status"]
        == "UNDETERMINED_ZERO_ELIGIBLE_OBSERVATIONS"
    )
    assert sem["human_acceptance"]["value"] is None
    assert "armed, NOT that" in out["note"]
