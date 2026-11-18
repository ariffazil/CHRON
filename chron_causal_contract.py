"""Causal diagnosis contract — admissibility of a CHRON lesson as causal knowledge.

WHY THIS EXISTS (measured 2026-11-16, case pred-089ce72ac833):

  CHRON recorded `VERIFIED_INCORRECT`, emitted `lesson-assumption_error-9`
  ("Predictions whose declared assumptions failed are recurring (9 times).
  Review assumption quality..."), and flagged it `actionable: true`,
  `promotion_eligible: true`. It carried no root cause, no owning component, no
  corrective action, and no evidence chain to the behaviour change that followed.

  The behaviour change was real (a code-lane fix, ~15:06 on 2026-11-11). The
  last duplicate auto_init event was 17:56 the same day. The lesson was written
  at 23:15. The fix preceded the lesson. Any reader that treats "wrong before,
  right after" as "the lesson worked" would award the learning loop credit for
  a repair it did not perform.

THREE BREAKPOINTS THIS CONTRACT NAMES:

  1. OUTCOME -> CAUSAL DIAGNOSIS
     An error class (`ASSUMPTION_ERROR`) is a folder label, not a diagnosis.
     A diagnosis names the failed claim, the mechanism, the owning component,
     the corrective action and the test that would falsify the correction.

  2. DIAGNOSIS -> ACTIONABLE LESSON
     An aggregate over 9 occurrences cannot carry one mechanism. Recurrence
     counts events; it does not add causal knowledge. `aggregation without
     learning` is the honest name for a lesson whose text is unchanged at
     recurrence 1 and 9 while only the number moves.

  3. LESSON -> BEHAVIOUR ATTRIBUTION
     A behaviour change observed after a lesson is not a behaviour change
     caused by that lesson. Attribution requires a chain:
        lesson -> policy candidate -> authorisation -> implementation artifact
               -> deployment receipt -> runtime observation
     Without the chain: behaviour_change_attribution = UNKNOWN.

WHAT THIS MODULE DELIBERATELY DOES NOT DO: it does not gate promotion.

  Tightening `actionable` to require a mechanism would flip every aggregate
  lesson to DIAGNOSTIC in one pass — `lesson_actionable` 7 -> 0,
  `blind_lesson_share` 0.79 -> 1.00, tripping MAX_BLIND_LESSON_SHARE. That is
  an owner decision (F13/kernel), not a side effect of a status field. This
  module ADDS disclosure so no downstream reader can mistake a class label for
  causal knowledge. A gate change belongs in a separate, authorised row.

Supersession, never rewrite: consumers must treat an older lesson without a
`causal_status` field as MECHANISM_ABSENT, not as unknown-quality.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

CONTRACT_VERSION = "chron-causal-contract.v1"

MECHANISM_PRESENT = "MECHANISM_PRESENT"
MECHANISM_ABSENT = "MECHANISM_ABSENT"
NOT_A_LESSON = "NOT_A_LESSON"

# A lesson may claim causal knowledge only when every one of these is present
# AND non-empty. A class label alone never qualifies.
MECHANISM_FIELDS = (
    "failed_claim",
    "root_cause",  # {component, mechanism, evidence[]}
    "corrective_action",  # {type, artifact, owner_lane}
)

# Attribution chain a behaviour change must carry before a lesson may take
# credit for it. Absent any link -> UNKNOWN.
ATTRIBUTION_CHAIN = (
    "lesson_id",
    "policy_candidate_id",
    "authorization",
    "implementation_artifact",
    "deployment_receipt",
    "runtime_observation",
)


def failure_rate(failures: int, opportunities: int) -> dict:
    """Every zero must carry its denominator.

    `n=1 -> n=0` is not an improvement; it is an absence of measurement unless
    the number of chances to fail is stated. This helper forces the denominator
    to travel with the numerator and refuses to emit a rate when exposure is 0.
    """
    failures = int(failures or 0)
    opportunities = int(opportunities or 0)
    if opportunities <= 0:
        return {
            "state": "UNMEASURED_NO_EXPOSURE",
            "failures": failures,
            "opportunities": opportunities,
            "rate": None,
            "note": "zero events without exposure is not a pass",
        }
    return {
        "state": "MEASURED",
        "failures": failures,
        "opportunities": opportunities,
        "rate": failures / opportunities,
        "note": "failures / eligible opportunities",
    }


ATTRIBUTION_UNKNOWN = "UNKNOWN"
ATTRIBUTION_NOT_ESTABLISHED = "NOT_ESTABLISHED"


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return len(value) > 0
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return True


def evaluate_lesson(lesson: Mapping[str, Any]) -> dict:
    """Classify one lesson record. Never mutates, never raises on junk input."""
    if not isinstance(lesson, Mapping) or not (
        lesson.get("lesson_id") or lesson.get("error_type")
    ):
        return {
            "contract": CONTRACT_VERSION,
            "status": NOT_A_LESSON,
            "missing": list(MECHANISM_FIELDS),
            "causal_trustworthy": False,
        }

    missing = [f for f in MECHANISM_FIELDS if not _non_empty(lesson.get(f))]
    root = lesson.get("root_cause")
    if isinstance(root, Mapping):
        for sub in ("component", "mechanism"):
            if not _non_empty(root.get(sub)):
                missing.append(f"root_cause.{sub}")

    status = MECHANISM_PRESENT if not missing else MECHANISM_ABSENT
    return {
        "contract": CONTRACT_VERSION,
        "status": status,
        "missing": missing,
        "causal_trustworthy": status == MECHANISM_PRESENT,
        # Recurrence is an event count. It is not causal knowledge, and the
        # record says so in a field a reader can join on.
        "recurrence_is_not_evidence": True,
        "actionable_claimed": bool(lesson.get("actionable")),
        # Honest disclosure: a lesson can be actionable-as-a-reminder and still
        # carry no mechanism. Those are different claims.
        "actionable_but_mechanism_absent": bool(lesson.get("actionable"))
        and status != MECHANISM_PRESENT,
    }


def attribution_record(chain: Optional[Mapping[str, Any]] = None) -> dict:
    """Attribution for a behaviour change. Default is UNKNOWN — never credit.

    Callers that actually have the full chain pass it; everything else gets an
    explicit UNKNOWN so a reader cannot infer causation from proximity in time.
    """
    chain = chain or {}
    missing = [link for link in ATTRIBUTION_CHAIN if not _non_empty(chain.get(link))]
    established = not missing
    return {
        "contract": CONTRACT_VERSION,
        "behaviour_change_attribution": (
            chain.get("actual_change_lane") if established else ATTRIBUTION_UNKNOWN
        ),
        "learning_loop_contribution": (
            "ESTABLISHED" if established else ATTRIBUTION_NOT_ESTABLISHED
        ),
        "chain_missing": missing,
        "note": (
            "correlation after a lesson is not causation by that lesson; "
            "credit requires the full chain"
        ),
    }


def report(lessons: Optional[list] = None) -> dict:
    """Aggregate contract coverage over lesson rows (pure; caller supplies rows)."""
    rows = [r for r in (lessons or []) if isinstance(r, Mapping)]
    absent = present = 0
    actionable_without_mechanism = 0
    for r in rows:
        ev = evaluate_lesson(r)
        if ev["status"] == MECHANISM_PRESENT:
            present += 1
        elif ev["status"] == MECHANISM_ABSENT:
            absent += 1
        if ev.get("actionable_but_mechanism_absent"):
            actionable_without_mechanism += 1
    total = present + absent
    return {
        "contract": CONTRACT_VERSION,
        "lessons_examined": total,
        "mechanism_present": present,
        "mechanism_absent": absent,
        "actionable_without_mechanism": actionable_without_mechanism,
        "mechanism_coverage": (present / total) if total else None,
        "note": (
            "mechanism_absent is the honest common case for aggregate lessons; "
            "it is disclosure, not failure"
        ),
    }
