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

Promotion requires:
  - Recurrence (same error_class seen 2+ times)
  - Measured effect (lesson applied → error reduced)
  - External validation (FRAME or arifOS confirms)

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from chron.chron_prediction import get_verified, compute_calibration, save_calibration
from chron.chron_episode import learn_from_error
from chron.chron_store import get_store

LESSONS_FILE = Path("/root/chron/data/lessons.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    seen = {l.get("fingerprint") for l in load_lessons() if l.get("fingerprint")}

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

        # Extract lesson from error pattern
        if error_type == "ASSUMPTION_ERROR":
            lesson_text = (
                f"Predictions with {error_type} assumptions are recurring "
                f"({recurrence} times). Review assumption quality before accepting "
                f"predictions with untested assumptions."
            )
        elif error_type == "DATA_ERROR":
            lesson_text = (
                f"Verification failed due to missing data ({recurrence} times). "
                f"Improve data collection pipeline before verifying predictions."
            )
        elif error_type == "MODEL_ERROR":
            lesson_text = (
                f"Prediction model was wrong ({recurrence} times). "
                f"Recalibrate model or switch to ensemble approach."
            )
        elif error_type == "REGIME_CHANGE":
            lesson_text = (
                f"Regime change detected ({recurrence} times). "
                f"Predictions based on historical patterns may be invalid."
            )
        else:
            lesson_text = (
                f"Unknown error type '{error_type}' seen {recurrence} times. "
                f"Investigate root cause."
            )

        # Compute mean Brier for this error class
        brier_scores = [
            p.get("brier_score", 0.25)
            for p in preds
            if p.get("brier_score") is not None
        ]
        mean_brier = sum(brier_scores) / len(brier_scores) if brier_scores else None

        lesson = {
            "lesson_id": f"lesson-{error_type.lower()}-{recurrence}",
            "error_type": error_type,
            "recurrence": recurrence,
            "mean_brier": mean_brier,
            "lesson": lesson_text,
            "status": "CANDIDATE",
            "created_at": _now_iso(),
            "promotion_eligible": recurrence >= 2,
            "prediction_ids": [p.get("prediction_id") for p in preds],
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

    # Save lessons
    _save_lessons(lessons)

    # Update calibration
    calibration = compute_calibration()
    save_calibration(calibration)

    return lessons


def _save_lessons(lessons: list[dict]) -> None:
    """Append lessons to JSONL store."""
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LESSONS_FILE, "a") as f:
        for lesson in lessons:
            f.write(json.dumps(lesson, default=str) + "\n")


def load_lessons() -> list[dict]:
    """Load all lessons from JSONL store."""
    if not LESSONS_FILE.exists():
        return []
    lessons = []
    with open(LESSONS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lessons.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return lessons


def get_candidates() -> list[dict]:
    """Get lesson candidates eligible for promotion."""
    return [
        l
        for l in load_lessons()
        if l.get("status") == "CANDIDATE" and l.get("promotion_eligible")
    ]


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
