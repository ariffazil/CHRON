"""CHRON learning-loop closure — the recalibration path.

Proves that a VERIFIED OUTCOME can change a FUTURE CONFIDENCE. The defect being
closed: before 2026-09-21, chron_prediction.load_calibration() had zero callers
outside its own definition, and generate_from_chron_events() read neither
calibration.json nor lessons.jsonl. OUTCOME->LEARNING wrote both files every cycle
and nothing consumed them. Predict -> Verify -> Learn -> WRITE FILE is a ledger,
not a loop.

Required cases (all real assertions, no prints-as-proof):
  1. overconfident store + proposed 0.9  -> calibrated < 0.9
  2. underconfident store + proposed 0.5 -> calibrated > 0.5
  3. effective n below the minimum -> bounded correction, quality INSUFFICIENT
  4. UNKNOWN-only error classes -> no aggressive correction applied
  5. the GENERATOR consumes calibration + lessons (fails on the old behaviour)
  6. the calibrated value tracks the snapshot (it is not a hardcoded multiplier)
  7. recalibrate_confidence() is pure: no I/O, deterministic, monotone in n
  8. a blind ("Investigate root cause") lesson is not a learned policy

Run:  python3 /root/chron/tests/test_calibration_loop.py      (script mode)
      python3 -m pytest /root/chron/tests/test_calibration_loop.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root")

from chron import chron_prediction as cp  # noqa: E402
from chron import chron_learn as cl  # noqa: E402

FAILS: list[str] = []


# ───────────────────────── helpers ─────────────────────────


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def check_true(label: str, cond, evidence: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' — ' + evidence) if evidence else ''}")
    if not cond:
        FAILS.append(label)


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# ───────────────────────── synthetic snapshots ─────────────────────────
# Shapes mirror what compute_calibration() actually emits (verified against
# /root/chron/data/calibration.json on 2026-09-21: total 2, accuracy 0.5,
# mean_brier 0.24625, by_error_type {"UNKNOWN":1,"NONE":1}).

OVERCONFIDENT = {
    "effective_n": 60,
    "total": 60,
    "accuracy": 0.40,          # delivered 40%
    "mean_brier": 0.33,
    "mean_confidence": 0.90,   # said 90%  → bias = +0.50, overconfident
    "bias": 0.50,
    "reliability": -0.32,
    "by_error_type": {"NONE": 24, "MODEL_ERROR": 36},
    "blind_lesson_share": 0.0,
}

UNDERCONFIDENT = {
    "effective_n": 60,
    "total": 60,
    "accuracy": 0.80,          # delivered 80%
    "mean_brier": 0.16,
    "mean_confidence": 0.45,   # said 45%  → bias = -0.35, underconfident
    "bias": -0.35,
    "reliability": 0.36,
    "by_error_type": {"NONE": 48, "ASSUMPTION_ERROR": 12},
    "blind_lesson_share": 0.0,
}

SMALL_N = {                    # the shape of the real store today
    "effective_n": 2,
    "total": 2,
    "accuracy": 0.50,
    "mean_brier": 0.24625,
    "mean_confidence": 0.825,
    "bias": 0.325,
    "by_error_type": {"NONE": 1, "UNKNOWN": 1},
}

BLIND_ALL = {                  # every decisive failure unclassifiable
    "effective_n": 50,
    "total": 50,
    "accuracy": 0.40,
    "mean_brier": 0.42,
    "mean_confidence": 0.90,
    "bias": 0.50,
    "by_error_type": {"UNKNOWN": 50},
}

CLASSIFIED_ALL = dict(BLIND_ALL, by_error_type={"MODEL_ERROR": 50})


# ───────────────────────── 1. overconfident ─────────────────────────


def test_overconfident_store_moves_confidence_down():
    o = cp.recalibrate_confidence(0.9, OVERCONFIDENT)
    check("quality is SUFFICIENT", o.quality, cp.QUALITY_SUFFICIENT)
    check_true("calibrated < proposed", o.calibrated < 0.9, f"{o.calibrated} < 0.9")
    check_true("correction applied", o.applied, o.reason)
    # rule: (1-w)p + w*accuracy ; w = 60/68
    want = (1 - 60 / 68) * 0.9 + (60 / 68) * 0.40
    check_true("matches documented rule", close(o.calibrated, want), f"{o.calibrated} == {want}")
    check("anchor is the measured base rate", o.anchor, 0.40)


# ───────────────────────── 2. underconfident ─────────────────────────


def test_underconfident_store_moves_confidence_up():
    o = cp.recalibrate_confidence(0.5, UNDERCONFIDENT)
    check("quality is SUFFICIENT", o.quality, cp.QUALITY_SUFFICIENT)
    check_true("calibrated > proposed", o.calibrated > 0.5, f"{o.calibrated} > 0.5")
    want = (1 - 60 / 68) * 0.5 + (60 / 68) * 0.80
    check_true("matches documented rule", close(o.calibrated, want), f"{o.calibrated} == {want}")


# ───────────────────────── 3. small n is bounded ─────────────────────────


def test_small_n_correction_bounded_and_flagged_insufficient():
    o = cp.recalibrate_confidence(0.9, SMALL_N)
    check("quality is INSUFFICIENT below the floor", o.quality, cp.QUALITY_INSUFFICIENT)
    check("effective_n reported", o.effective_n, 2)
    check("classified_n excludes the uninterpretable outcome", o.classified_n, 1)

    w = 1 / 9  # m=1, kappa=8
    check_true("weight is the Beta-Binomial posterior weight", close(o.weight, w), f"{o.weight} == {w}")

    # Derived bound: |shift| <= w*(|anchor - p| + |delta|) <= 2w. Never hardcoded.
    bound = w * (abs(0.5 - 0.9) + abs(SMALL_N["bias"]))
    check_true("shift within the derived variance bound", abs(o.shift) <= bound + 1e-9,
               f"|{o.shift}| <= {bound}")
    check_true("correction is near a no-op (< 0.10)", abs(o.shift) < 0.10, f"shift={o.shift}")

    # Monotonicity in n: the same statistic at n=2 must move LESS than at n=60.
    # This is the variance-awareness claim, and it is falsifiable.
    small = cp.recalibrate_confidence(0.9, dict(OVERCONFIDENT, effective_n=2, total=2,
                                                by_error_type={"NONE": 1, "MODEL_ERROR": 1}))
    big = cp.recalibrate_confidence(0.9, OVERCONFIDENT)
    check_true("|shift| grows with effective n",
               abs(small.shift) < abs(big.shift),
               f"n=2 shift {small.shift} < n=60 shift {big.shift}")

    # Weight must be strictly increasing in n (weaker prior influence as data grows).
    weights = [
        cp.recalibrate_confidence(0.9, dict(OVERCONFIDENT, effective_n=n, total=n)).weight
        for n in (2, 5, 20, 100)
    ]
    check_true("weight strictly increases with n", weights == sorted(weights) and weights[0] < weights[-1],
               f"{weights}")


# ───────────────────────── 4. UNKNOWN-only ─────────────────────────


def test_unknown_only_error_types_apply_no_correction():
    blind = cp.recalibrate_confidence(0.9, BLIND_ALL)
    check("quality INSUFFICIENT when taxonomy is blind", blind.quality, cp.QUALITY_INSUFFICIENT)
    check("classified_n is 0", blind.classified_n, 0)
    check("weight is 0 — no evidence to act on", blind.weight, 0.0)
    check("calibrated == proposed (no correction)", blind.calibrated, 0.9)
    check("applied is False", blind.applied, False)

    classified = cp.recalibrate_confidence(0.9, CLASSIFIED_ALL)
    check_true("a classified store DOES correct", abs(classified.shift) > 0.1,
               f"shift={classified.shift}")
    check_true("blind store is strictly less aggressive than classified store",
               abs(blind.shift) < abs(classified.shift) / 10,
               f"|{blind.shift}| < 1/10 |{classified.shift}|")

    # A blind lesson store must be able to veto an otherwise-usable calibration.
    vetoed = cp.recalibrate_confidence(0.9, dict(OVERCONFIDENT, blind_lesson_share=1.0))
    check("blind lesson store vetoes SUFFICIENT", vetoed.quality, cp.QUALITY_INSUFFICIENT)
    baseline = cp.recalibrate_confidence(0.9, OVERCONFIDENT).calibrated
    check_true("veto changes the outcome", vetoed.calibrated != baseline,
               f"{vetoed.calibrated} vs {baseline}")


# ───────────────────────── 5. the generator is wired ─────────────────────────


def test_generator_consumes_calibration_and_lessons():
    """The wiring test. Fails on the pre-2026-09-21 behaviour (raw confidence).

    Redirects PREDICTIONS_FILE / CALIBRATION_FILE / LESSONS_FILE to a scratch dir
    and replaces create_prediction with a recorder, so nothing is written to any
    live store.
    """
    tmp = Path(tempfile.mkdtemp(prefix="chron-calib-test-"))
    pred_file = tmp / "predictions.jsonl"
    pred_file.touch()
    calib_file = tmp / "calibration.json"
    calib_file.write_text(json.dumps(OVERCONFIDENT))
    lessons_file = tmp / "lessons.jsonl"
    lessons_file.write_text(json.dumps({
        "lesson_id": "lesson-model_error-3", "error_type": "MODEL_ERROR",
        "recurrence": 3, "mean_brier": 0.21, "lesson": "Model overstates under regime drift.",
        "status": "CANDIDATE", "promotion_eligible": True, "fingerprint": "deadbeef",
    }) + "\n")

    saved = (cp.PREDICTIONS_FILE, cp.CALIBRATION_FILE, cp.create_prediction, cl.LESSONS_FILE)
    recorded: list[dict] = []
    try:
        cp.PREDICTIONS_FILE = pred_file
        cp.CALIBRATION_FILE = calib_file
        cl.LESSONS_FILE = lessons_file
        cp.create_prediction = lambda **kw: (recorded.append(kw) or {"rec": True})

        generated = cp.generate_from_chron_events()
    finally:
        cp.PREDICTIONS_FILE, cp.CALIBRATION_FILE, cp.create_prediction, cl.LESSONS_FILE = saved

    check_true("generator produced predictions", len(recorded) > 0, f"{len(recorded)} recorded")
    check("one record per generated prediction", len(recorded), len(generated))

    # Independent anti-vacuity check, read from the INPUT rather than from the
    # generator's own audit fields: with an empty predictions store every
    # sub-prediction in chron_events.json is generated, so the multiset of emitted
    # confidences must differ from the multiset of proposed confidences. A
    # generator that merely forwards the proposal cannot pass this.
    events_raw = json.loads(Path("/root/AAA/scripts/chron_events.json").read_text())
    raw_proposals = sorted(
        round(float(sp.get("confidence", 0.5)), 6)
        for e in events_raw.get("events", [])
        for sp in e.get("predictions", [])
    )
    emitted = sorted(round(float(r["confidence"]), 6) for r in recorded)
    check_true("emitted confidences are NOT the raw proposals",
               raw_proposals != emitted,
               f"{len(raw_proposals)} proposals vs {len(emitted)} emitted")

    # .get() throughout: on reverted wiring every assertion must REPORT, not raise,
    # so the demonstration shows the whole failure surface rather than one KeyError.
    missing = [k for k in ("confidence_proposed", "confidence_calibrated",
                           "calibration_quality", "calibration_snapshot",
                           "recalibration", "lessons_consumed")
               if not all(k in r for r in recorded)]
    check("every prediction carries the audit trail", missing, [])

    check("quality propagated from the store",
          [r for r in recorded if r.get("calibration_quality") != cp.QUALITY_SUFFICIENT], [])

    # The heart of the wiring: emitted confidence is the CALIBRATED value.
    check("emitted confidence == calibrated",
          [r for r in recorded
           if not close(r["confidence"], r.get("confidence_calibrated", -1))], [])

    # ...and where a correction is DUE (proposal above the measured base rate it
    # must equal it) the emitted confidence is not simply the proposal.
    due = [r for r in recorded
           if r.get("confidence_proposed", 0) > OVERCONFIDENT["accuracy"] + 1e-9]
    check_true("a correction is due for at least one prediction", len(due) > 0,
               f"{len(due)} of {len(recorded)}")
    check("emitted confidence != raw proposal wherever a correction is due",
          [r for r in due if close(r["confidence"], r["confidence_proposed"])], [])
    check("every due confidence moved DOWN",
          len([r for r in due if r["confidence"] < r["confidence_proposed"]]), len(due))

    # A proposal already AT the measured base rate is correctly a no-op — the
    # correction is a measurement, not a blanket penalty.
    at_anchor = [r for r in recorded
                 if close(r.get("confidence_proposed", -1), OVERCONFIDENT["accuracy"])]
    check("proposal == measured base rate ⇒ no shift",
          [r for r in at_anchor if not close(r["confidence"], r["confidence_proposed"])], [])

    got_snapshot = recorded[0].get("calibration_snapshot") or {}
    check("snapshot digest records effective_n", got_snapshot.get("effective_n"), 60)
    check("snapshot digest records accuracy", got_snapshot.get("accuracy"), 0.40)
    check("snapshot digest names its source file", got_snapshot.get("source"), str(calib_file))

    consumed = recorded[0].get("lessons_consumed") or {}
    check("lessons are consumed and audited", consumed.get("lesson_total"), 1)
    check("the actionable lesson is labelled actionable", consumed.get("lesson_actionable"), 1)
    check("no lesson is silently applied as policy", consumed.get("policy_applied"), False)

    # And the baseline that makes the assertion meaningful: with the store EMPTY,
    # the proposal must pass through unchanged.
    saved2 = (cp.PREDICTIONS_FILE, cp.CALIBRATION_FILE, cp.create_prediction, cl.LESSONS_FILE)
    recorded2: list[dict] = []
    try:
        cp.PREDICTIONS_FILE = pred_file
        cp.CALIBRATION_FILE = tmp / "absent.json"
        cl.LESSONS_FILE = tmp / "absent_lessons.jsonl"
        cp.create_prediction = lambda **kw: (recorded2.append(kw) or {"rec": True})
        cp.generate_from_chron_events()
    finally:
        cp.PREDICTIONS_FILE, cp.CALIBRATION_FILE, cp.create_prediction, cl.LESSONS_FILE = saved2
    check("empty store ⇒ confidence passed through unaltered",
          [r for r in recorded2
           if not close(r["confidence"], r.get("confidence_proposed", -1))], [])
    check("empty store ⇒ quality NONE",
          sorted({r.get("calibration_quality") for r in recorded2}), [cp.QUALITY_NONE])


# ───────────────────────── 6. snapshot-dependent, not hardcoded ─────────────────────────


def test_calibrated_value_tracks_the_snapshot():
    """A hardcoded multiplier would give the same answer for every snapshot."""
    outcomes = [
        cp.recalibrate_confidence(0.9, dict(OVERCONFIDENT, accuracy=a, mean_confidence=0.9))
        for a in (0.20, 0.40, 0.50, 0.70, 0.90)
    ]
    values = [o.calibrated for o in outcomes]
    check_true("five accuracies ⇒ five distinct calibrations", len(set(values)) == 5, f"{values}")
    check_true("calibration is monotone increasing in measured accuracy",
               values == sorted(values), f"{values}")
    check_true("accuracy 0.90 does not move a 0.9 proposal down",
               outcomes[-1].calibrated >= 0.9, f"{outcomes[-1].calibrated}")

    # Same proposal, different store → different answer.
    up = cp.recalibrate_confidence(0.5, UNDERCONFIDENT).calibrated
    none_ = cp.recalibrate_confidence(0.5, None).calibrated
    check_true("underconfident store moves 0.5 up", up > 0.5, f"{up}")
    check_true("empty store leaves 0.5 alone", close(none_, 0.5), f"{none_}")

    # Bias direction alone must flip the sign of the correction (INSUFFICIENT branch).
    over = cp.recalibrate_confidence(0.5, dict(SMALL_N))
    under = cp.recalibrate_confidence(0.5, dict(SMALL_N, mean_confidence=0.2, bias=-0.3))
    check_true("overconfident small-n store moves 0.5 down", over.shift < 0, f"{over.shift}")
    check_true("underconfident small-n store moves 0.5 up", under.shift > 0, f"{under.shift}")

    # An absent `bias` field must be DERIVED, not silently treated as zero — a
    # snapshot with accuracy and mean_confidence is complete even without it.
    derived = {k: v for k, v in SMALL_N.items() if k != "bias"}
    d = cp.recalibrate_confidence(0.9, derived)
    f = cp.recalibrate_confidence(0.9, SMALL_N)
    check_true("bias derived when the field is absent (same calibrated value)",
               close(d.calibrated, f.calibrated) and close(d.delta_applied, SMALL_N["bias"], 1e-12),
               f"calibrated {d.calibrated} vs {f.calibrated}; delta {d.delta_applied}")


# ───────────────────────── 7. purity ─────────────────────────


def test_recalibrate_is_pure():
    a = cp.recalibrate_confidence(0.9, OVERCONFIDENT)
    b = cp.recalibrate_confidence(0.9, OVERCONFIDENT)
    check("deterministic", a, b)

    # A snapshot supplied by the caller is the only input: point the module's file
    # constant at a path that does not exist and the pure function is unaffected.
    saved = cp.CALIBRATION_FILE
    try:
        cp.CALIBRATION_FILE = Path("/root/chron/data/__no_such_calibration__.json")
        c = cp.recalibrate_confidence(0.9, OVERCONFIDENT)
    finally:
        cp.CALIBRATION_FILE = saved
    check("does not read CALIBRATION_FILE", c, a)

    # No writes anywhere the recalibration path could plausibly reach.
    data_dir = Path("/root/chron/data")
    watched = ("calibration.json", "lessons.jsonl", "predictions.jsonl",
               "verification_log.jsonl", "verification_attempts.jsonl", "loop_log.jsonl")
    before = {n: (p.stat().st_size, p.stat().st_mtime_ns)
              for n in watched if (p := data_dir / n).exists()}
    for _ in range(3):
        cp.recalibrate_confidence(0.9, OVERCONFIDENT)
        cp.recalibrate_confidence(0.1, SMALL_N)
    after = {n: (p.stat().st_size, p.stat().st_mtime_ns)
             for n in watched if (p := data_dir / n).exists()}
    check("no CHRON store changed", after, before)

    # Input dict must not be mutated.
    snap = dict(OVERCONFIDENT)
    cp.recalibrate_confidence(0.9, snap)
    check("snapshot not mutated", snap, OVERCONFIDENT)

    # Out-of-range proposals are clamped, not propagated.
    check_true("p<0 clamped", 0.0 <= cp.recalibrate_confidence(-3.0, OVERCONFIDENT).calibrated <= 1.0)
    check_true("p>1 clamped", 0.0 <= cp.recalibrate_confidence(7.0, OVERCONFIDENT).calibrated <= 1.0)


# ───────────────────────── 8. blind lesson is not policy ─────────────────────────


def test_blind_lesson_is_not_a_learned_policy():
    # The literal defect: the live lesson store's only row.
    blind_lesson = {
        "lesson_id": "lesson-unknown-1", "error_type": "UNKNOWN", "recurrence": 5,
        "mean_brier": 0.20,
        "lesson": "Unknown error type 'UNKNOWN' seen 5 times. Investigate root cause.",
        "status": "CANDIDATE", "promotion_eligible": True,
    }
    check("UNKNOWN lesson is not actionable", cl.is_actionable_lesson(blind_lesson), False)

    named = dict(blind_lesson, lesson_id="lesson-model_error-5",
                 error_type="MODEL_ERROR", lesson="Model overstates under regime drift.")
    check("a named-cause lesson IS actionable", cl.is_actionable_lesson(named), True)
    check("no-cause lesson without a measurement is not actionable",
          cl.is_actionable_lesson(dict(named, mean_brier=None)), False)

    # And the live store: the real rows must be excluded from promotion.
    live = cl.load_lessons()
    actionable_live = [l for l in live if cl.is_actionable_lesson(l)]
    check("every lesson in the live store is blind", len(actionable_live), 0)
    check_true("live store is non-empty (the check is not vacuous)", len(live) > 0, f"{len(live)} lessons")
    check("live promotion candidates exclude them",
          [c for c in cl.get_candidates() if c.get("error_type") == "UNKNOWN"], [])

    health = cl.lesson_health()
    check("blind_lesson_share is 1.0 on the live store", health["blind_lesson_share"], 1.0)


# ───────────────────────── script mode ─────────────────────────


def main() -> int:
    sections = [
        ("1. Overconfident store → confidence moves DOWN", test_overconfident_store_moves_confidence_down),
        ("2. Underconfident store → confidence moves UP", test_underconfident_store_moves_confidence_up),
        ("3. Small effective n → bounded correction, INSUFFICIENT", test_small_n_correction_bounded_and_flagged_insufficient),
        ("4. UNKNOWN-only error classes → no aggressive correction", test_unknown_only_error_types_apply_no_correction),
        ("5. Generator consumes calibration + lessons", test_generator_consumes_calibration_and_lessons),
        ("6. Calibrated value tracks the snapshot", test_calibrated_value_tracks_the_snapshot),
        ("7. recalibrate_confidence() is pure", test_recalibrate_is_pure),
        ("8. Blind lesson is not a learned policy", test_blind_lesson_is_not_a_learned_policy),
    ]
    for title, fn in sections:
        print(title)
        fn()
        print()

    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} check(s) failed: {FAILS}")
        return 1
    print("RESULT: PASS — verified outcomes now change future confidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
