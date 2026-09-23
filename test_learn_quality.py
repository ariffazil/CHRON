"""CHRON lesson-quality invariants — the "Investigate root cause" defect.

WHAT THIS FILE PINS
-------------------
The live lesson store held exactly one lesson:
    "Unknown error type 'UNKNOWN' seen 1 times. Investigate root cause."
That is not a lesson. It is the classifier reporting that it had no bucket, in
the grammar of learning: a row that can be counted, cited, and (before the
actionability gate) promoted, while teaching nothing. The invariant below is
structural, not textual — any phrasing of "we do not know why" is equally
unlearnable, so the test keys on the error CLASS and on the absence of a stated
reason, never on the sentence.

WHAT IT ASSERTS
---------------
  1. No EFFECTIVE lesson may carry an unexplained class (UNKNOWN / missing), and
     any historical row that does must carry a non-empty explanation and be
     superseded — retained as evidence, not as a lesson.
  2. An unclassifiable outcome produces NO lesson row; it is recorded in the
     insufficiency ledger with the reason it could not be classified.
  3. A classifiable outcome still produces a lesson (the fix must not turn the
     lesson store into a store that never learns).
  4. The promotion gate is a gate, not a wall: it returns False on the live
     store and True on a synthetic lesson whose criteria are genuinely met.
     A criterion that can never be satisfied is indistinguishable from no gate.
  5. Criterion 3 (external_validation) is reported as a dropped criterion, never
     as a hardcoded False that reads like an unimplemented feature.
  6. Every class in INTERPRETABLE_ERROR_TYPES has a lesson rule, so adding a
     class cannot silently demote it into the insufficiency bucket.

Run: /usr/bin/python3 /root/chron/test_learn_quality.py
     (pytest also collects it: pytest /root/chron/test_learn_quality.py)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root")

from chron import chron_learn as cl  # noqa: E402
from chron import chron_loop_close as lc  # noqa: E402
from chron import chron_prediction as cp  # noqa: E402

FAILS: list[str] = []

# Classes that name no cause. A lesson may never be built from one of these.
UNEXPLAINED = {"UNKNOWN", "UNCLASSIFIED", "", "NONE"}

# The live lesson ledger. The override exists so this file can be SHOWN failing
# against a fixture that contains the original defect (an unexplained lesson with
# no reason), without writing that defect into production to prove a point:
#     CHRON_LESSONS_PATH=/tmp/blind-lessons.jsonl python3 test_learn_quality.py
# The path actually read is printed, so an audit can see which store was judged.
LESSONS_PATH = Path(
    os.environ.get("CHRON_LESSONS_PATH", "/root/chron/data/lessons.jsonl")
)


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def check_true(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(label)


def _read_text(path: Path) -> str:
    """Read a scratch ledger that may legitimately not exist yet."""
    return path.read_text().strip() if path.exists() else ""


def _explanation(row: dict) -> str:
    """The stated reason a row could not be classified, if any."""
    for key in ("error_type_reason", "superseded_reason", "reason", "classification_basis"):
        val = str(row.get(key) or "").strip()
        if val:
            return val
    return ""


# ───────────────────────── 1. live store ─────────────────────────


def test_live_lesson_store_has_no_blind_lesson():
    """The real lessons.jsonl: no lesson may be served from an unexplained class.

    Read as the store now presents itself (last write wins per lesson_id). The
    ledger is append-only, so an unexplained row written before this gate existed
    cannot be edited away — it is SUPERSEDED instead, and the test requires
    exactly that: the row the store serves for that lesson_id must carry a stated
    reason and must no longer count as a lesson. A lesson written with an
    unexplained class and no reason FAILS here.
    """
    raw_path = LESSONS_PATH
    if not raw_path.exists():  # pragma: no cover - store must exist on this host
        check_true("live lesson store exists", False, str(raw_path))
        return
    raw = [json.loads(l) for l in raw_path.read_text().splitlines() if l.strip()]
    print(f"  (lesson ledger read: {raw_path} — rows: {len(raw)})")

    by_id: dict[str, list[dict]] = {}
    for row in raw:
        by_id.setdefault(str(row.get("lesson_id")), []).append(row)

    for lesson_id, rows in by_id.items():
        current = rows[-1]  # last write wins — what the store serves
        et = str(current.get("error_type") or "").strip().upper()
        status = str(current.get("status") or "").upper()
        if et in UNEXPLAINED:
            check_true(
                f"lesson {lesson_id}: unexplained row carries a stated reason",
                bool(_explanation(current)),
                _explanation(current)[:90],
            )
            check(
                f"lesson {lesson_id}: unexplained row is not served as a lesson",
                status,
                "SUPERSEDED",
            )
            check_true(
                f"lesson {lesson_id}: history retained in the ledger",
                len(rows) > 1,
                f"{len(rows)} row(s)",
            )
        elif status in ("CANDIDATE", "LESSON"):
            check_true(f"lesson {lesson_id} names a cause", et not in UNEXPLAINED, et)
            check_true(f"lesson {lesson_id} has a lesson rule", et in cl._LESSON_TEXT, et)
            check_true(f"lesson {lesson_id} is actionable", cl.is_actionable_lesson(current))

    # No EFFECTIVE (served) lesson may be blind.
    effective = cl.load_lessons()
    blind_effective = [
        l
        for l in effective
        if str(l.get("status") or "").upper() not in ("SUPERSEDED", "RETRACTED")
        and str(l.get("error_type") or "").strip().upper() in UNEXPLAINED
    ]
    check("effective blind lessons (must be 0)", len(blind_effective), 0)


def test_lesson_health_discloses_blindness():
    """Blindness must stay visible after the unexplained rows moved out of lessons."""
    health = cl.lesson_health()
    print(f"  (live lesson_health: {health})")
    check_true(
        "lesson health reports the unexplained outcome count",
        "unclassified_outcomes" in health,
    )
    share = health.get("blind_lesson_share")
    check_true(
        "blind share is disclosed (None or a number, never silently dropped)",
        0.0 <= share <= 1.0 if share is not None else True,
        f"blind_lesson_share={share}",
    )


# ───────────────────────── 2/3. extraction behaviour ─────────────────────────


def _scratch(tmp: Path):
    """Redirect every store chron_learn / chronology touch. Returns a patch dict."""
    import chron.chron_store as cs

    preds = tmp / "predictions.jsonl"
    vlog = tmp / "verification_log.jsonl"
    lessons = tmp / "lessons.jsonl"
    insuff = tmp / "insufficient_data.jsonl"
    pol = tmp / "policy_candidates.jsonl"
    for p in (preds, vlog, lessons):
        p.touch()

    saved = {
        "cp.PREDICTIONS_FILE": cp.PREDICTIONS_FILE,
        "cp.VERIFICATION_LOG": cp.VERIFICATION_LOG,
        "cp.CALIBRATION_FILE": cp.CALIBRATION_FILE,
        "cl.LESSONS_FILE": cl.LESSONS_FILE,
        "cl.INSUFFICIENT_FILE": cl.INSUFFICIENT_FILE,
        "cl.get_store": cl.get_store,
        "lc.POLICY_CANDIDATES_FILE": lc.POLICY_CANDIDATES_FILE,
        "lc.LOOP_LOG": lc.LOOP_LOG,
        "cs.get_store": cs.get_store,
    }
    cp.PREDICTIONS_FILE = preds
    cp.VERIFICATION_LOG = vlog
    cp.CALIBRATION_FILE = tmp / "calibration.json"
    cl.LESSONS_FILE = lessons
    cl.INSUFFICIENT_FILE = insuff
    lc.POLICY_CANDIDATES_FILE = pol
    lc.LOOP_LOG = tmp / "loop_log.jsonl"

    class _Store:
        def append(self, episode):
            return episode

    scratch_store = _Store()
    cl.get_store = lambda *a, **k: scratch_store
    cs.get_store = lambda *a, **k: scratch_store
    return saved, preds, vlog, lessons, insuff, pol


def _restore(saved) -> None:
    import chron.chron_store as cs

    cp.PREDICTIONS_FILE = saved["cp.PREDICTIONS_FILE"]
    cp.VERIFICATION_LOG = saved["cp.VERIFICATION_LOG"]
    cp.CALIBRATION_FILE = saved["cp.CALIBRATION_FILE"]
    cl.LESSONS_FILE = saved["cl.LESSONS_FILE"]
    cl.INSUFFICIENT_FILE = saved["cl.INSUFFICIENT_FILE"]
    cl.get_store = saved["cl.get_store"]
    lc.POLICY_CANDIDATES_FILE = saved["lc.POLICY_CANDIDATES_FILE"]
    lc.LOOP_LOG = saved["lc.LOOP_LOG"]
    cs.get_store = saved["cs.get_store"]


def _write_outcome(preds: Path, vlog: Path, pid: str, error_type: str,
                   verdict: str, verified_at: str, confidence: float = 0.7) -> None:
    """One birth record + one verification record, written to a scratch store."""
    with open(preds, "a") as f:
        f.write(json.dumps({
            "prediction_id": pid, "claim": f"claim {pid}", "expected_outcome": "e",
            "confidence": confidence, "verify_at": verified_at, "status": "ACTIVE",
            "assumptions": ["a"], "brier_score": None,
        }) + "\n")
    with open(vlog, "a") as f:
        f.write(json.dumps({
            "prediction_id": pid, "verdict": verdict, "error_type": error_type,
            "observed_outcome": "measured", "error": 1.0 if verdict.endswith("INCORRECT") else 0.0,
            "brier_score": (1 - confidence) ** 2 if verdict.endswith("INCORRECT") else confidence ** 2,
            "verified_at": verified_at,
        }) + "\n")


def test_unclassifiable_outcome_is_not_written_as_a_lesson():
    """The defect itself: UNKNOWN must produce an insufficiency, not a lesson."""
    tmp = Path(tempfile.mkdtemp(prefix="chron-learn-quality-"))
    saved, preds, vlog, lessons, insuff, _pol = _scratch(tmp)
    try:
        _write_outcome(preds, vlog, "pred-blind-1", "UNKNOWN",
                       "VERIFIED_INCORRECT", "2026-09-20T12:00:00Z")
        new_lessons = cl.extract_lessons()
    finally:
        _restore(saved)

    check("no lesson produced from an unexplained class", new_lessons, [])
    check("lessons.jsonl stays empty", _read_text(lessons), "")

    rows = [json.loads(l) for l in _read_text(insuff).splitlines() if l.strip()]
    check("one insufficiency recorded", len(rows), 1)
    if rows:
        check("state is explicit", rows[0].get("state"), "INSUFFICIENT_DATA_TO_CLASSIFY")
        check("class is named", rows[0].get("error_type_seen"), "UNKNOWN")
        check_true("reason is stated", len(str(rows[0].get("reason") or "")) > 20,
                   str(rows[0].get("reason"))[:90])
        check("the outcome is still cited", rows[0].get("prediction_ids"), ["pred-blind-1"])

    # Idempotent: the same unexplained outcome must not accumulate rows.
    saved, preds, vlog, lessons, insuff, _pol = _scratch(tmp)
    try:
        cl.extract_lessons()
    finally:
        _restore(saved)
    rows2 = [l for l in _read_text(insuff).splitlines() if l.strip()]
    check("re-running does not duplicate the insufficiency", len(rows2), 1)


def test_classifiable_outcome_still_produces_a_lesson():
    """The fix must not make the store unable to learn at all."""
    tmp = Path(tempfile.mkdtemp(prefix="chron-learn-quality-"))
    saved, preds, vlog, lessons, insuff, _pol = _scratch(tmp)
    try:
        _write_outcome(preds, vlog, "pred-a-1", "ASSUMPTION_ERROR",
                       "VERIFIED_INCORRECT", "2026-09-19T12:00:00Z")
        _write_outcome(preds, vlog, "pred-a-2", "ASSUMPTION_ERROR",
                       "VERIFIED_INCORRECT", "2026-09-20T12:00:00Z")
        new_lessons = cl.extract_lessons()
    finally:
        _restore(saved)

    check("one lesson produced", len(new_lessons), 1)
    if new_lessons:
        lesson = new_lessons[0]
        check("class is the measured one", lesson.get("error_type"), "ASSUMPTION_ERROR")
        check("recurrence counted", lesson.get("recurrence"), 2)
        check("scope is prediction_quality", lesson.get("lesson_scope"), "prediction_quality")
        check("status is CANDIDATE", lesson.get("status"), "CANDIDATE")
        check_true("lesson text states a rule, not a to-do",
                   "Investigate root cause" not in lesson.get("lesson", ""),
                   lesson.get("lesson", "")[:80])
        check_true("lesson is promotion-eligible on its own gate",
                   lesson.get("promotion_eligible") is True)
    check("nothing routed to the insufficiency ledger", _read_text(insuff), "")


# ───────────────────────── 4/5. promotion gate ─────────────────────────


def test_promotion_gate_is_not_a_permanent_hold():
    """The gate must be able to return True AND False against real evidence."""
    tmp = Path(tempfile.mkdtemp(prefix="chron-learn-quality-"))
    saved, preds, vlog, lessons, insuff, pol = _scratch(tmp)
    try:
        created = "2026-06-01T00:00:00Z"
        # BEFORE the lesson: every decisive outcome carries ASSUMPTION_ERROR (share 1.0)
        for i in range(4):
            _write_outcome(preds, vlog, f"pred-before-{i}", "ASSUMPTION_ERROR",
                           "VERIFIED_INCORRECT", f"2026-05-0{i + 1}T00:00:00Z")
        # AFTER the lesson: the class became rarer (1 of 4 decisive = share 0.25)
        _write_outcome(preds, vlog, "pred-after-0", "ASSUMPTION_ERROR",
                       "VERIFIED_INCORRECT", "2026-06-02T00:00:00Z")
        for i in range(1, 4):
            _write_outcome(preds, vlog, f"pred-after-{i}", "NONE",
                           "VERIFIED_CORRECT", f"2026-06-0{i + 2}T00:00:00Z")

        improving = {
            "lesson_id": "lesson-assumption_error-2", "error_type": "ASSUMPTION_ERROR",
            "recurrence": 2, "mean_brier": 0.30, "lesson": "assumptions failed",
            "status": "CANDIDATE", "promotion_eligible": True, "created_at": created,
        }
        ok = lc._evaluate_promotion(improving)
        check("gate CAN return True (criteria met)", ok["eligible"], True)
        criteria = ok["criteria"]
        check("exactly two criteria evaluated", sorted(criteria.keys()),
              ["measured_effect", "recurrence"])
        check("recurrence criterion met", criteria["recurrence"]["met"], True)
        check("measured effect is the state IMPROVED",
              criteria["measured_effect"]["state"], "IMPROVED")
        check_true("measured effect carries its numbers",
                   criteria["measured_effect"]["evidence"].get("decisive_after") == 4,
                   json.dumps(criteria["measured_effect"]["evidence"]))
        check("criterion 3 is reported as DROPPED",
              ok.get("criterion_dropped"), "external_validation")

        # Same store, a lesson whose class did NOT become rarer → False.
        not_improving = dict(improving, error_type="MODEL_ERROR", lesson_id="lesson-model-2")
        bad = lc._evaluate_promotion(not_improving)
        check("gate returns False when no improvement is measured", bad["eligible"], False)

        # A lesson from an unexplained class is refused by the precondition.
        blind = dict(improving, error_type="UNKNOWN", lesson_id="lesson-unknown-2")
        blind_result = lc._evaluate_promotion(blind)
        check("unexplained class is refused", blind_result["eligible"], False)
        check("the refusal names the precondition",
              "precondition:classifiable" in blind_result["reason"], True)

        # And the promotion leaves an artifact (it used to leave only a log line).
        written = lc._write_policy_candidates([ok])
        check("one policy candidate written", written, 1)
        rows = [json.loads(l) for l in _read_text(pol).splitlines() if l.strip()]
        check("candidate ledger has one row", len(rows), 1)
        if rows:
            check("candidate carries evidence",
                  rows[0]["evidence"]["decisive_after"], 4)
            check_true("candidate does not self-authorise deployment",
                       "authorised deployment" in rows[0].get("requires", ""),
                       rows[0].get("requires", "")[:60])
    finally:
        _restore(saved)


def test_no_placeholder_criterion_in_the_gate():
    """No criterion may be a hardcoded False standing in for a real check."""
    lesson = {
        "lesson_id": "lesson-x", "error_type": "MODEL_ERROR", "recurrence": 1,
        "mean_brier": 0.49, "created_at": "2099-01-01T00:00:00Z",
    }
    result = lc._evaluate_promotion(lesson)
    criteria = result["criteria"]
    for name, value in criteria.items():
        check_true(f"criterion {name} reports evidence, not a bare bool",
                   not isinstance(value, bool), f"{type(value).__name__}")
    check_true("external_validation is not a boolean criterion",
               not isinstance(criteria.get("external_validation"), bool))
    check("dropped criterion is named", result.get("criterion_dropped"), "external_validation")
    check_true("the HOLD reason does not name a dropped criterion",
               "external_validation" not in result["reason"], result["reason"])
    check_true("the module states why criterion 3 was dropped",
               "DROPPED" in Path(lc.__file__).read_text())


def test_lesson_rules_cover_every_interpretable_class():
    """Adding a class to the vocabulary must force adding its rule."""
    missing = [c for c in cp.INTERPRETABLE_ERROR_TYPES if c not in cl._LESSON_TEXT]
    check("every interpretable class has a lesson rule", missing, [])


def test_recurrence_threshold_is_measured_not_assumed():
    """Recurrence is arithmetic on the ledger; report the real numbers."""
    ledger = [
        v for v in cp.get_verified(limit=10000)
        if v.get("verdict") in lc.DECISIVE_VERDICTS
    ]
    by_class: dict[str, int] = {}
    for row in ledger:
        et = str(row.get("error_type") or "UNKNOWN").strip().upper()
        by_class[et] = by_class.get(et, 0) + 1
    print(f"  (live decisive outcomes: {len(ledger)}; per class: {by_class}; "
          f"threshold: {lc.CRITERION_RECURRENCE_MIN})")
    check("recurrence threshold is 2", lc.CRITERION_RECURRENCE_MIN, 2)
    interpretable = {
        k: v for k, v in by_class.items() if k in cp.INTERPRETABLE_ERROR_TYPES
    }
    print(f"  (per INTERPRETABLE class: {interpretable or '{}'}) — "
          f"max reachable recurrence for a promotable class: "
          f"{max(interpretable.values()) if interpretable else 0}")
    for et in by_class:
        if et not in cp.INTERPRETABLE_ERROR_TYPES:
            print(f"  (note: class {et!r} reached recurrence {by_class[et]} but names no cause)")


# ───────────────────────── script mode ─────────────────────────


def main() -> int:
    sections = [
        ("1. Live lesson store carries no blind lesson", test_live_lesson_store_has_no_blind_lesson),
        ("2. Lesson health discloses blindness", test_lesson_health_discloses_blindness),
        ("3. Unclassifiable outcome is not a lesson", test_unclassifiable_outcome_is_not_written_as_a_lesson),
        ("4. Classifiable outcome still becomes a lesson", test_classifiable_outcome_still_produces_a_lesson),
        ("5. Promotion gate can fire (True) and refuse (False)", test_promotion_gate_is_not_a_permanent_hold),
        ("6. No placeholder criterion", test_no_placeholder_criterion_in_the_gate),
        ("7. Every interpretable class has a rule", test_lesson_rules_cover_every_interpretable_class),
        ("8. Recurrence measured from the ledger", test_recurrence_threshold_is_measured_not_assumed),
    ]
    for title, fn in sections:
        print(title)
        fn()
        print()

    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} check(s): {FAILS}")
        return 1
    print("RESULT: PASS — no unclassifiable outcome is written as a lesson")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
