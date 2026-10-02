"""CHRON Learn — lesson extraction from verified predictions.

Closes the A3 arrow: OUTCOME → LEARNING.

For each verified prediction:
  1. Compute Brier score
  2. Classify error (DATA_ERROR, ASSUMPTION_ERROR, MODEL_ERROR, REGIME_CHANGE)
  3. Extract lesson if error is meaningful
  4. Create learn episode
  5. Track lesson candidates for promotion to policy

Lesson lifecycle:
  CANDIDATE → LESSON → POLICY_CANDIDATE → POLICY

Promotion requires (enforced in chron_loop_close, not here):
  - Recurrence (same error class seen 2+ times)
  - Measured effect (the class became RARER after the lesson existed)

Criterion 3 — external validation — was DROPPED on 2026-09-21, NOT implemented.
No organ can falsify a lesson, and a criterion that can never be satisfied makes
a gate that only ever says HOLD. See chron_loop_close.CRITERION_3_DROPPED.

A verified outcome whose class is NOT interpretable is not a lesson. It is
recorded in INSUFFICIENT_FILE as an explicit "insufficient data to classify"
state: the store's blindness is disclosed as blindness and never written in the
grammar of learning. (Before this, the one and only row in the live lesson store
read "Unknown error type 'UNKNOWN' seen 1 times. Investigate root cause.")

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow `python3 chron_learn.py <subcommand>` from anywhere, matching the
# bootstrap chron_loop_close.py / chron_cron_verify.py already use.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chron.chron_prediction import (
    get_verified,
    compute_calibration,
    save_calibration,
    INTERPRETABLE_ERROR_TYPES,
)
from chron.chron_episode import learn_from_error
from chron.chron_store import get_store

LESSONS_FILE = Path("/root/chron/data/lessons.jsonl")
# Outcomes the classifier could not explain. Deliberately a SEPARATE ledger from
# lessons.jsonl: "we cannot say why this was wrong" is a measurement of the
# store's blindness, and writing it as a lesson (which is what happened until
# 2026-09-21) turns missing knowledge into the appearance of knowledge.
INSUFFICIENT_FILE = Path("/root/chron/data/insufficient_data.jsonl")


def _insufficient_fingerprint(error_type: str, prediction_ids: list) -> str:
    """Stable identity for an insufficiency: class + the outcomes carrying it."""
    import hashlib

    key = f"{error_type}|{','.join(sorted(str(p) for p in prediction_ids))}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ───────────────────────── LESSON VOCABULARY ─────────────────────────
#
# Closed set: ONLY a class in this map yields a lesson. A class absent from it
# has no text because no rule can be stated from it, and the outcome is routed
# to INSUFFICIENT_FILE instead. Adding a class to
# chron_prediction.INTERPRETABLE_ERROR_TYPES without adding its rule here is the
# failure this map is designed to make impossible — coverage is asserted in
# test_learn_quality.py.
_LESSON_TEXT = {
    "ASSUMPTION_ERROR": (
        "Predictions whose declared assumptions failed are recurring ({n} times). "
        "Review assumption quality — and re-check each assumption at verification "
        "time — before accepting predictions of this shape."
    ),
    "MODEL_ERROR": (
        "The prediction model was wrong ({n} times). Recalibrate confidence or "
        "switch to an ensemble before trusting this model's output."
    ),
    "REGIME_CHANGE": (
        "A source reported the underlying event delayed or cancelled ({n} times) — "
        "a regime change, not a calibration error. Predictions based on the "
        "previously assumed regime are invalid."
    ),
    "DATA_ERROR": (
        "VERIFIER-PIPELINE defect: verification could not reach evidence ({n} "
        "times) because the source was missing or unparseable. Fix the data lane "
        "before reading any accuracy figure from these outcomes."
    ),
    "RANGE_STALE_AT_MINT": (
        "Threshold/range claims on live-market subjects were minted without a "
        "spot-check ({n} times): the range was stale before the clock started. "
        "Spot-check the live price/level at creation, record it in "
        "evidence_snapshot, and only then commit a range."
    ),
    "AWAITING_EVIDENCE": (
        "VERIFIER-PIPELINE observation: {n} predictions passed their verify_at and "
        "no evidence was observed. Absence of evidence is not a miss — these stay "
        "open and cannot support a prediction-quality lesson until a source speaks."
    ),
}

# Which kind of rule a lesson is. Only prediction_quality lessons are candidates
# for promotion (chron_loop_close): a verifier-pipeline lesson is an ops repair
# to the verification lane, and its measurement would have to come from the
# attempts ledger, not from decisive outcomes — so promoting it through the
# prediction-policy gate would be measuring the wrong thing.
VERIFIER_PIPELINE_CLASSES = ("DATA_ERROR", "AWAITING_EVIDENCE")


def lesson_scope(error_type: str) -> str:
    """'verifier_pipeline' for verifier defects, else 'prediction_quality'."""
    et = str(error_type or "").strip().upper()
    return (
        "verifier_pipeline" if et in VERIFIER_PIPELINE_CLASSES else "prediction_quality"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── LESSON ACTIONABILITY ─────────────────────────
# 2026-09-21 (learning-loop closure). The live lessons.jsonl holds exactly one
# lesson:
#     "Unknown error type 'UNKNOWN' seen 1 times. Investigate root cause."
# That is the classifier reporting its own blindness, not a rule learned from an
# outcome. Before this gate it carried status CANDIDATE and could satisfy the
# promotion criteria in chron_loop_close._evaluate_promotion (recurrence >= 2 and
# mean_brier < 0.25), which would have promoted "investigate root cause" into
# POLICY. The loop would then have been consuming its own inability to classify
# as a learned rule.
#
# A lesson is ACTIONABLE only when it names a cause. UNKNOWN does not name a
# cause; NONE is the absence of error. The test is structural (error class), not
# textual, because the text is a symptom: any phrasing of "we do not know why"
# is equally unlearnable.


def is_actionable_lesson(lesson: dict) -> bool:
    """Can this lesson legitimately change future behaviour? Pure."""
    error_type = str(lesson.get("error_type") or "UNKNOWN").strip().upper()
    if error_type not in INTERPRETABLE_ERROR_TYPES:
        return False
    # A lesson with no measured Brier has no measured effect either.
    return lesson.get("mean_brier") is not None


def lesson_health() -> dict:
    """Health of the lesson store, as consumed by the calibration snapshot.

    `blind_lesson_share` > MAX_BLIND_LESSON_SHARE is what lets the lesson store
    VETO an otherwise-usable calibration: if learning itself is blind, the skill
    estimate must not be treated as usable.

    2026-09-21 (lesson-quality fix): blindness is now reported from BOTH places
    it can appear — a non-actionable lesson row, and an outcome recorded in the
    insufficiency ledger. Moving unexplained outcomes out of lessons.jsonl must
    not remove the veto's evidence: "the store could not explain why it was
    wrong" is exactly what this share exists to expose.
    """
    lessons = load_lessons()
    active = [
        l
        for l in lessons
        if str(l.get("status") or "").upper() not in ("SUPERSEDED", "RETRACTED")
    ]
    actionable = [l for l in active if is_actionable_lesson(l)]
    blind = [l for l in active if not is_actionable_lesson(l)]
    unclassified = load_insufficient()

    blind_ids = [l.get("lesson_id") for l in blind] + [
        f"insufficient-{r.get('fingerprint')}" for r in unclassified
    ]
    denominator = len(active) + len(unclassified)

    return {
        "lesson_total": len(lessons),
        "lesson_active": len(active),
        "lesson_actionable": len(actionable),
        "lesson_blind": len(blind),
        "unclassified_outcomes": len(unclassified),
        "blind_lesson_share": (len(blind_ids) / denominator) if denominator else None,
        "actionable_lesson_ids": [l.get("lesson_id") for l in actionable],
        "blind_lesson_ids": blind_ids,
    }


def load_active_policies() -> list[dict]:
    """Lessons that may legitimately act as policy right now.

    Empty is the honest answer at present: promotion additionally requires
    external validation (chron_loop_close._evaluate_promotion), which is not
    implemented, so nothing has reached status POLICY.
    """
    return [
        l
        for l in load_lessons()
        if l.get("status") == "POLICY" and is_actionable_lesson(l)
    ]


# ───────────────────────── LESSON EXTRACTION ─────────────────────────


def _lesson_fingerprint(lesson: dict) -> str:
    """Stable identity for a lesson: error class + recurrence + mean Brier.

    Two extractions of the same error pattern are the same lesson; this is what
    lets the loop skip them instead of re-appending every cycle.
    """
    import hashlib

    mb = lesson.get("mean_brier")
    key = (
        f"{lesson.get('error_type')}|{lesson.get('recurrence')}|"
        f"{round(float(mb), 4) if mb is not None else 'NA'}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def extract_lessons() -> list[dict]:
    """Extract NEW lessons from all verified predictions.

    Returns only lessons not already held (see _lesson_fingerprint). Callers
    that want the full inventory should use load_lessons().
    """
    verified = get_verified(limit=100)
    if not verified:
        return []

    store = get_store()
    lessons = []
    insufficient: list[dict] = []
    seen = {l.get("fingerprint") for l in load_lessons() if l.get("fingerprint")}
    seen_insufficient = {
        r.get("fingerprint") for r in load_insufficient() if r.get("fingerprint")
    }

    # Group by error_type for pattern detection
    by_error: dict[str, list[dict]] = {}
    for p in verified:
        et = p.get("error_type", "UNKNOWN")
        by_error.setdefault(et, []).append(p)

    for error_type, preds in by_error.items():
        if error_type == "NONE":
            continue  # No lesson from correct predictions

        # Count recurrence
        recurrence = len(preds)

        # Compute mean Brier for this error class
        brier_scores = [
            p.get("brier_score", 0.25)
            for p in preds
            if p.get("brier_score") is not None
        ]
        mean_brier = sum(brier_scores) / len(brier_scores) if brier_scores else None
        prediction_ids = [p.get("prediction_id") for p in preds]

        # ── UNCLASSIFIABLE OUTCOME → an explicit insufficiency, NOT a lesson ──
        #
        # 2026-09-21 (lesson-quality defect). This branch used to write a lesson
        # reading "Unknown error type 'UNKNOWN' seen N times. Investigate root
        # cause." into lessons.jsonl — the only row the live store held. That is
        # the classifier reporting its own blindness in the grammar of a lesson:
        # a row whose content is "we do not know why" can be counted, cited and
        # (before the actionability gate below) promoted, while teaching nothing.
        #
        # The fix is not a better sentence for a failed classification — it is to
        # refuse to call it a lesson. The outcome IS measured (it happened, it is
        # in the ledger); it is UNEXPLAINED. Those are different states and only
        # one of them is learning, so the unexplained one is recorded separately
        # with the reason it could not be classified.
        lesson_text = _LESSON_TEXT.get(error_type)
        if lesson_text is None:
            record = {
                "state": "INSUFFICIENT_DATA_TO_CLASSIFY",
                "fingerprint": _insufficient_fingerprint(error_type, prediction_ids),
                "error_type_seen": error_type,
                "reason": (
                    f"error class {error_type!r} names no cause, so {recurrence} "
                    f"verified outcome(s) are measured but unexplained; a lesson "
                    f"requires an interpretable class "
                    f"({', '.join(INTERPRETABLE_ERROR_TYPES)})"
                ),
                "recurrence": recurrence,
                "mean_brier": mean_brier,
                "prediction_ids": prediction_ids,
                "recorded_at": _now_iso(),
            }
            if record["fingerprint"] in seen_insufficient:
                continue
            seen_insufficient.add(record["fingerprint"])
            insufficient.append(record)
            continue

        # ── Actionability / promotion gate (2026-09-21, learning-loop closure) ──
        # A lesson whose error class names no cause cannot become a rule. Previously
        # status was hardcoded CANDIDATE and promotion_eligible was recurrence >= 2,
        # so a blind class recurring twice became a policy candidate carrying the
        # text "Investigate root cause" — the loop promoting its own blindness.
        # Blind lessons are now DIAGNOSTIC: retained and disclosed, never promoted.
        # The threshold that consumes this is MAX_BLIND_LESSON_SHARE in
        # chron.chron_prediction (via chron_learn.lesson_health).
        actionable = error_type in INTERPRETABLE_ERROR_TYPES and mean_brier is not None
        scope = lesson_scope(error_type)
        status = "CANDIDATE" if actionable else "DIAGNOSTIC"
        # Only a prediction-quality rule can be promoted: a verifier-pipeline
        # lesson is an ops repair to the evidence lane, and the promotion gate
        # measures decisive outcomes (see lesson_scope).
        promotion_eligible = (
            actionable and scope == "prediction_quality" and recurrence >= 2
        )
        lesson_text = lesson_text.format(n=recurrence)

        lesson = {
            "lesson_id": f"lesson-{error_type.lower()}-{recurrence}",
            "error_type": error_type,
            "recurrence": recurrence,
            "mean_brier": mean_brier,
            "lesson": lesson_text,
            "lesson_scope": scope,
            "status": status,
            "created_at": _now_iso(),
            "promotion_eligible": promotion_eligible,
            "prediction_ids": prediction_ids,
            # 2026-09-21 (learning-loop closure): the promotion gate in
            # chron_loop_close._evaluate_promotion checks only recurrence and
            # measured effect, both of which a blind lesson can satisfy. Recording
            # actionability here is what stops "Investigate root cause" from
            # ever being read as policy downstream.
            "actionable": actionable,
        }
        fp = _lesson_fingerprint(lesson)
        lesson["fingerprint"] = fp

        # Dedup (2026-09-18 loop-closure repair, L4/L5): this function runs on
        # every loop cycle and previously appended unconditionally, so N runs
        # produced N copies of the same lesson and N learn episodes — inflating
        # lessons_total and candidate counts with no new information. A lesson
        # whose fingerprint we already hold is not new work.
        if fp in seen:
            continue

        # Create learn episode only for genuinely new lessons
        if preds:
            ep = learn_from_error(
                preds[0],  # representative prediction
                error_type,
                lesson_text,
                mean_brier or 0.25,
            )
            store.append(ep)

        seen.add(fp)
        lessons.append(lesson)

    # Save lessons, then the insufficiencies (append-only, both)
    _save_lessons(lessons)
    _save_insufficient(insufficient)

    # Update calibration
    calibration = compute_calibration()
    save_calibration(calibration)

    return lessons


def _save_lessons(lessons: list[dict]) -> None:
    """Append lessons to JSONL store (append-only; supersede, never rewrite)."""
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LESSONS_FILE, "a") as f:
        for lesson in lessons:
            f.write(json.dumps(lesson, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    """Tolerant JSONL reader shared by the lesson and insufficiency ledgers."""
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _save_insufficient(records: list[dict]) -> None:
    """Append unclassifiable outcomes to their own ledger.

    Separate file, never merged into lessons.jsonl: a reader of the lesson store
    must be able to trust that every row is a rule.
    """
    if not records:
        return
    INSUFFICIENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INSUFFICIENT_FILE, "a") as f:
        for record in records:
            f.write(json.dumps(record, default=str) + "\n")


def load_lessons() -> list[dict]:
    """Load the EFFECTIVE lesson store: one row per lesson_id, last write wins.

    The ledger is append-only, so a lesson is superseded by appending a row that
    carries the same lesson_id with a later status — the idiom
    verification_log.jsonl already uses for VOID. Returning every row would
    resurrect a superseded lesson and count it twice.
    """
    effective: dict[str, dict] = {}
    for idx, row in enumerate(_read_jsonl(LESSONS_FILE)):
        lid = row.get("lesson_id")
        effective[lid if lid else f"_row-{idx}"] = row
    return list(effective.values())


def load_insufficient() -> list[dict]:
    """Load the verified outcomes the classifier could NOT explain.

    These are measurements of the store's blindness, not lessons. Surfaced
    (clock-side and in lesson_health) so the gap is visible rather than silent.
    """
    return _read_jsonl(INSUFFICIENT_FILE)


def get_candidates() -> list[dict]:
    """Get lesson candidates eligible for promotion.

    2026-09-21 (learning-loop closure): the actionability test is applied here as
    well as at extraction time, so a legacy store row that predates the gate (the
    live store holds exactly one: lesson-unknown-1, text "Investigate root
    cause") cannot reach the promotion loop by carrying a stale
    promotion_eligible flag.
    """
    return [
        l
        for l in load_lessons()
        if l.get("status") == "CANDIDATE"
        and l.get("promotion_eligible")
        and is_actionable_lesson(l)
    ]


def supersede_lesson(
    lesson_id: str, reason: str, error_type_reason: Optional[str] = None
) -> Optional[dict]:
    """Supersede a lesson by APPENDING a later row with the same lesson_id.

    Never rewrites the ledger. Used when a row already in the store must stop
    counting as a lesson — e.g. the two historical UNKNOWN rows, whose text was
    the classifier's own blindness ("Investigate root cause"). The original row
    stays in the file as evidence; load_lessons() takes the later status.
    """
    current = next((l for l in load_lessons() if l.get("lesson_id") == lesson_id), None)
    if current is None:
        return None
    row = {
        **current,
        "status": "SUPERSEDED",
        "supersedes_status": current.get("status"),
        "superseded_at": _now_iso(),
        "superseded_reason": reason,
        "promotion_eligible": False,
        "actionable": False,
        "error_type_reason": error_type_reason
        or current.get("error_type_reason")
        or (
            f"error class {current.get('error_type')!r} names no cause, so this row "
            "records the classifier's blindness rather than a rule; it is retained "
            "as evidence and superseded as a lesson"
        ),
    }
    _save_lessons([row])
    return row


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    import sys

    args = sys.argv[1:]

    if args and args[0] == "candidates":
        candidates = get_candidates()
        print(f"Lesson candidates eligible for promotion: {len(candidates)}")
        for c in candidates:
            print(f"  [{c['lesson_id']}] {c['lesson'][:60]}")
            print(
                f"    recurrence: {c['recurrence']}  brier: {c.get('mean_brier', '?')}"
            )
    elif args and args[0] == "insufficient":
        rows = load_insufficient()
        print(f"Unexplained outcomes (not lessons): {len(rows)}")
        for r in rows:
            print(
                f"  [{r.get('state')}] class={r.get('error_type_seen')} n={r.get('recurrence')}"
            )
            print(f"    {r.get('reason')}")
            print(f"    predictions: {r.get('prediction_ids')}")
    elif args and args[0] == "health":
        print(json.dumps(lesson_health(), indent=2))
    else:
        lessons = extract_lessons()
        print(f"CHRON Learn — extracted {len(lessons)} lessons")
        for l in lessons:
            print(f"  [{l['lesson_id']}] {l['lesson'][:60]}")
            print(
                f"    recurrence: {l['recurrence']}  eligible: {l['promotion_eligible']}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
