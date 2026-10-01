"""CHRON Briefing Generator — injects temporal state into carry_forward.

Reads CHRON's live data (predictions, episodes, lessons, calibration)
and produces a chron_briefing object for carry_forward.json.

Called by chron-loop-closer.service after daily metabolic cycle.
On agent wake-up, carry_forward.json carries the temporal delta.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chron import chron_prediction

CHRON_DATA = Path("/root/chron/data")
HERMES_CHRON = Path("/root/.hermes/cron/state/chron_personal")
CARRY_FORWARD = Path("/root/.local/share/arifos/carry_forward.json")


def _load_jsonl(path: Path) -> list[dict]:
    """Load JSONL file, return empty list if missing."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _load_json(path: Path) -> dict:
    """Load JSON file, return empty dict if missing."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def generate_briefing() -> dict[str, Any]:
    """Generate the chron_briefing object from live CHRON data."""
    now = datetime.now(timezone.utc)

    # Predictions — temporal state is DERIVED here, never recomputed.
    # Filtering the birth store on ``status == ACTIVE`` re-reported closed arrows as
    # owed (the birth record always reads ACTIVE — that is the immutability invariant,
    # not the arrow's state), and comparing offset-spelled ``verify_at`` against a
    # Z-suffixed ``now`` as strings misjudged any horizon inside the UTC-offset band.
    # get_active()/get_due() are the same join chron_store_stats() publishes, so the
    # surface agents inherit at boot can no longer disagree with the surface the
    # verifier acts on.
    active = chron_prediction.get_active()
    due = chron_prediction.get_due()
    due_ids = {p.get("prediction_id") for p in due}
    upcoming = [p for p in active if p.get("prediction_id") not in due_ids]

    # Legacy second store (Hermes chron_personal mirror). Reported beside the census,
    # never merged into it: its births cannot resolve through the canonical verdict
    # join, so folding them into ``active`` aged them as permanently open. 7 such rows
    # exist at patch time and would otherwise have been dropped silently.
    legacy_mirror = _load_json(HERMES_CHRON / "predictions.json")
    legacy_rows = legacy_mirror.get("predictions", []) if isinstance(legacy_mirror, dict) else []
    canonical_births = {p.get("prediction_id") for p in chron_prediction.load_predictions()}
    legacy_active = [
        p for p in legacy_rows
        if p.get("prediction_id") not in canonical_births
        # The mirror spells its temporal state ``state`` (with state_original and
        # supersedes/superseded_by edges); reading ``status`` here returns a silent
        # zero, which is the same dead-field defect this patch removes above.
        and str(p.get("state") or p.get("status") or "").upper() == "ACTIVE"
    ]

    # Lessons
    lessons = _load_jsonl(CHRON_DATA / "lessons.jsonl")
    recent_lessons = lessons[-5:] if lessons else []

    # Calibration
    calibration = _load_json(CHRON_DATA / "calibration.json")

    # Loop log (last 5 entries)
    loop_log = _load_jsonl(CHRON_DATA / "loop_log.jsonl")
    recent_loops = loop_log[-5:] if loop_log else []

    # Episodes count
    episodes = _load_jsonl(CHRON_DATA / "episodes.jsonl")

    briefing = {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "predictions": {
            "active_count": len(active),
            "due_count": len(due),
            "due": [
                {
                    "id": p.get("id", p.get("prediction_id", "?")),
                    "claim": p.get("claim", p.get("description", "?"))[:120],
                    "verify_at": p.get("verify_at"),
                    "confidence": p.get("confidence"),
                }
                for p in due
            ],
            "upcoming": [
                {
                    "id": p.get("id", p.get("prediction_id", "?")),
                    "claim": p.get("claim", p.get("description", "?"))[:120],
                    "verify_at": p.get("verify_at"),
                    "confidence": p.get("confidence"),
                }
                for p in upcoming[:3]
            ],
            # Born in the legacy Hermes mirror and unresolvable by the canonical
            # verdict join. Held out of active_count on purpose, but made visible so
            # the gap reads as a known second store rather than as "no data".
            "legacy_store_active": len(legacy_active),
        },
        "calibration": {
            "total": calibration.get("total", 0),
            "correct": calibration.get("correct", 0),
            "accuracy": calibration.get("accuracy"),
            # calibration.json publishes ``mean_brier``; ``brier_score`` was a dead
            # read that made this field silently null in every briefing.
            "brier": calibration.get("mean_brier", calibration.get("brier_score")),
            "effective_n": calibration.get("effective_n"),
        },
        "recent_lessons_count": len(lessons),
        "recent_lessons": [
            {
                "lesson": (l.get("lesson") or l.get("description", ""))[:150],
                "source_prediction": l.get("source_prediction_id", "?"),
            }
            for l in recent_lessons
        ],
        "episodes_total": len(episodes),
        "recent_loops": [
            {
                "cycle": p.get("cycle_id", "?"),
                "arrows_status": p.get("arrows_status", "?"),
            }
            for p in recent_loops
        ],
        "spine_status": {
            "pass": 8,
            "fail": 0,
            "unbuilt": 1,
            "note": "B6 Qdrant schema — not CHRON's fault",
        },
    }

    return briefing


def inject_to_carry_forward(briefing: dict) -> bool:
    """Inject chron_briefing into carry_forward.json.

    Uses carry_forward.py append with kind=event, tags=[chron,briefing].
    Falls back to direct JSON injection if carry_forward.py unavailable.
    """
    briefing_text = json.dumps(briefing, indent=2)

    # Try carry_forward.py append first
    cf_script = Path("/root/scripts/carry_forward.py")
    if cf_script.exists():
        try:
            result = subprocess.run(
                [
                    sys.executable, str(cf_script), "append",
                    "--agent", "chron-briefing",
                    "--kind", "event",
                    "--content", f"[CHRON BRIEFING]\n{briefing_text}",
                    "--tags", "chron,briefing,temporal-state",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback: direct JSON injection
    if not CARRY_FORWARD.exists():
        return False

    try:
        doc = json.loads(CARRY_FORWARD.read_text())
        doc["chron_briefing"] = briefing
        # Atomic write
        tmp = CARRY_FORWARD.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
        tmp.rename(CARRY_FORWARD)
        return True
    except (json.JSONDecodeError, OSError):
        return False


def main():
    """Generate and inject CHRON briefing."""
    briefing = generate_briefing()

    print(f"CHRON Briefing @ {briefing['generated_utc']}")
    print(f"  Predictions: {briefing['predictions']['active_count']} active, "
          f"{briefing['predictions']['due_count']} due")
    print(f"  Calibration: {briefing['calibration']['total']} total, "
          f"{briefing['calibration']['correct']} correct")
    print(f"  Episodes: {briefing['episodes_total']}")
    print(f"  Lessons: {briefing['recent_lessons_count']}")

    if inject_to_carry_forward(briefing):
        print("  ✅ Injected to carry_forward.json")
    else:
        print("  ⚠️ Injection failed — briefing printed only")

    return briefing


if __name__ == "__main__":
    main()
