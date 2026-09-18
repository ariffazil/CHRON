"""CHRON TEMPORAL_ROOT — Kernel-level temporal validity tensor.

Every payload in the arifOS federation MUST inherit TemporalValidity.
This module defines the schema, validation, and injection logic.

From TEMPORAL-SUBSTRATE-DOCTRINE-2026-09-18.md:
  "Time is NOT an organ. Time is the foundational metric space of the federation."
  "Every payload, memory, state change, and authority grant must inherit TemporalValidity."

The Five Fields:
  valid_from        — when this became true in reality
  valid_until       — when this stops being true (None = still valid)
  superseded_by     — what replaced it (None = current)
  known_at          — when the system learned it
  causal_predecessor — what caused this (Lamport ordering)

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Any


# ───────────────────────── SCHEMA ─────────────────────────

TEMPORAL_ROOT_SCHEMA = {
    "version": "1.0.0",
    "ratified": "2026-09-18",
    "source": "TEMPORAL-SUBSTRATE-DOCTRINE-2026-09-18.md",
    "description": "Kernel-level temporal validity tensor for all federation payloads",
    "required_fields": {
        "valid_from": {
            "type": "ISO-8601 UTC",
            "description": "When this became true in reality",
            "example": "2026-09-18T08:00:00Z",
        },
        "known_at": {
            "type": "ISO-8601 UTC",
            "description": "When the system learned it",
            "example": "2026-09-18T08:05:00Z",
        },
    },
    "optional_fields": {
        "valid_until": {
            "type": "ISO-8601 UTC | null",
            "description": "When this stops being true (null = still valid)",
            "example": "2026-09-25T00:00:00Z",
        },
        "superseded_by": {
            "type": "string | null",
            "description": "ID of the record that replaced this one",
            "example": "chron-ep-20260919-observe-a1b2c3d4",
        },
        "causal_predecessor": {
            "type": "string | null",
            "description": "ID of the event that caused this (Lamport ordering)",
            "example": "flow-receipt-20260918-001",
        },
        "temporal_hash": {
            "type": "string",
            "description": "SHA-256 of the temporal fields for integrity",
        },
    },
    "invariants": {
        "T1": "valid_from <= known_at (you cannot learn something before it happens)",
        "T2": "valid_from <= valid_until (when valid_until is set)",
        "T3": "If superseded_by is set, valid_until MUST be set",
        "T4": "causal_predecessor must reference an existing record",
        "T5": "Prediction_Error = Observed_Future - Predicted_Future",
    },
}


# ───────────────────────── TEMPORAL VALIDITY ─────────────────────────


class TemporalValidity:
    """Temporal validity tensor — attaches to every federation payload.

    Usage:
        tv = TemporalValidity(valid_from="2026-09-18T08:00:00Z")
        payload = {"data": "...", **tv.to_dict()}

    Validation:
        tv.validate()  # raises ValueError if invariants violated
    """

    def __init__(
        self,
        valid_from: str | None = None,
        valid_until: str | None = None,
        superseded_by: str | None = None,
        known_at: str | None = None,
        causal_predecessor: str | None = None,
    ):
        now = _now_iso()
        self.valid_from = valid_from or now
        self.valid_until = valid_until
        self.superseded_by = superseded_by
        self.known_at = known_at or now
        self.causal_predecessor = causal_predecessor

    def to_dict(self) -> dict:
        """Serialize to dict for payload injection."""
        d = {
            "valid_from": self.valid_from,
            "known_at": self.known_at,
            "temporal_hash": self._hash(),
        }
        if self.valid_until is not None:
            d["valid_until"] = self.valid_until
        if self.superseded_by is not None:
            d["superseded_by"] = self.superseded_by
        if self.causal_predecessor is not None:
            d["causal_predecessor"] = self.causal_predecessor
        return d

    def validate(self) -> list[str]:
        """Validate temporal invariants. Returns list of violations (empty = valid)."""
        violations = []

        # T1: valid_from <= known_at
        if _parse_iso(self.valid_from) > _parse_iso(self.known_at):
            violations.append(
                f"T1 violation: valid_from ({self.valid_from}) > known_at ({self.known_at})"
            )

        # T2: valid_from <= valid_until
        if self.valid_until is not None:
            if _parse_iso(self.valid_from) > _parse_iso(self.valid_until):
                violations.append(
                    f"T2 violation: valid_from ({self.valid_from}) > valid_until ({self.valid_until})"
                )

        # T3: superseded_by requires valid_until
        if self.superseded_by is not None and self.valid_until is None:
            violations.append("T3 violation: superseded_by set but valid_until is None")

        return violations

    def is_current(self, at_time: str | None = None) -> bool:
        """Check if this record is valid at the given time (default: now)."""
        now = _parse_iso(at_time or _now_iso())
        from_time = _parse_iso(self.valid_from)

        if now < from_time:
            return False  # Not yet valid

        if self.valid_until is not None:
            until_time = _parse_iso(self.valid_until)
            if now >= until_time:
                return False  # Expired

        if self.superseded_by is not None:
            return False  # Superseded

        return True

    def prediction_error(
        self, observed_future: float, predicted_future: float
    ) -> float:
        """Compute prediction error: Observed_Future - Predicted_Future."""
        return observed_future - predicted_future

    def _hash(self) -> str:
        """SHA-256 of temporal fields for integrity."""
        canonical = json.dumps(
            {
                "valid_from": self.valid_from,
                "valid_until": self.valid_until,
                "known_at": self.known_at,
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()[:16]

    def __repr__(self) -> str:
        return (
            f"TemporalValidity(from={self.valid_from}, until={self.valid_until}, "
            f"known={self.known_at}, superseded={self.superseded_by})"
        )


# ───────────────────────── CAUSAL ORDERING ─────────────────────────


class CausalOrder:
    """Lamport causal ordering — not wall clock.

    Wall clocks lie. Network latency, clock skew, processing delays
    create false realities.

    Enforce:
      A → B: A causally precedes B (A's event influenced B)
      A || B: Concurrent / unordered (neither caused the other)

    Usage:
        order = CausalOrder()
        order.record("event-A")
        order.record("event-B", caused_by="event-A")
        order.happened_before("event-A", "event-B")  # True
    """

    def __init__(self):
        self._counter: int = 0
        self._events: dict[str, dict] = {}

    def record(
        self,
        event_id: str,
        caused_by: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Record an event with causal ordering."""
        self._counter += 1
        entry = {
            "event_id": event_id,
            "lamport_time": self._counter,
            "caused_by": caused_by,
            "recorded_at": _now_iso(),
            "metadata": metadata or {},
        }
        self._events[event_id] = entry

        # If caused_by, increment from predecessor's time
        if caused_by and caused_by in self._events:
            pred_time = self._events[caused_by]["lamport_time"]
            self._counter = max(self._counter, pred_time + 1)
            entry["lamport_time"] = self._counter

        return entry

    def happened_before(self, event_a: str, event_b: str) -> bool:
        """Check if A happened before B (causal ordering)."""
        if event_a not in self._events or event_b not in self._events:
            return False
        return (
            self._events[event_a]["lamport_time"]
            < self._events[event_b]["lamport_time"]
        )

    def concurrent(self, event_a: str, event_b: str) -> bool:
        """Check if A and B are concurrent (neither caused the other)."""
        if event_a not in self._events or event_b not in self._events:
            return True  # Unknown ordering = concurrent
        a_time = self._events[event_a]["lamport_time"]
        b_time = self._events[event_b]["lamport_time"]
        # Concurrent if neither is a causal predecessor of the other
        return a_time == b_time or (
            not self.happened_before(event_a, event_b)
            and not self.happened_before(event_b, event_a)
        )

    def get_chain(self, event_id: str) -> list[str]:
        """Get the causal chain leading to an event."""
        chain = []
        current = event_id
        while current and current in self._events:
            chain.append(current)
            current = self._events[current].get("caused_by")
        return list(reversed(chain))


# ───────────────────────── TEMPORAL COMMITMENT ─────────────────────────


class TemporalCommitment:
    """Binding temporal commitment — CHRON as ledger of future obligations.

    Not a cron scheduler. Keeper of the causal frontier.

    When agent decides "wait and observe," it creates a binding temporal commitment:

    temporal_commitment:
      trigger:
        condition: <event X>
        deadline: <time t>
      state_expectation:
        world_at_t: <what should be true>
      failure_mode:
        if_deadline_passed: <what to execute>
        if_condition_met: <what to execute>
    """

    def __init__(
        self,
        commitment_id: str,
        condition: str,
        deadline: str,
        world_expectation: str,
        if_deadline_passed: str,
        if_condition_met: str,
        created_by: str = "chron",
    ):
        self.commitment_id = commitment_id
        self.condition = condition
        self.deadline = deadline
        self.world_expectation = world_expectation
        self.if_deadline_passed = if_deadline_passed
        self.if_condition_met = if_condition_met
        self.created_by = created_by
        self.created_at = _now_iso()
        self.state = "ACTIVE"  # ACTIVE | FULFILLED | EXPIRED | CANCELLED

    def to_dict(self) -> dict:
        return {
            "commitment_id": self.commitment_id,
            "trigger": {
                "condition": self.condition,
                "deadline": self.deadline,
            },
            "state_expectation": {
                "world_at_t": self.world_expectation,
            },
            "failure_mode": {
                "if_deadline_passed": self.if_deadline_passed,
                "if_condition_met": self.if_condition_met,
            },
            "created_by": self.created_by,
            "created_at": self.created_at,
            "state": self.state,
        }

    def check(self, current_time: str | None = None) -> dict:
        """Check if commitment is due or expired."""
        now = _parse_iso(current_time or _now_iso())
        deadline = _parse_iso(self.deadline)

        if self.state != "ACTIVE":
            return {"state": self.state, "action": "NONE"}

        if now >= deadline:
            self.state = "EXPIRED"
            return {
                "state": "EXPIRED",
                "action": self.if_deadline_passed,
                "commitment_id": self.commitment_id,
            }

        return {
            "state": "ACTIVE",
            "action": "WAIT",
            "remaining_hours": (deadline - now).total_seconds() / 3600,
        }


# ───────────────────────── INJECTION HELPERS ─────────────────────────


def inject_temporal_root(payload: dict, **kwargs) -> dict:
    """Inject TEMPORAL_ROOT fields into any payload.

    Usage:
        payload = {"data": "some content"}
        payload = inject_temporal_root(payload, valid_from="2026-09-18T08:00:00Z")
    """
    tv = TemporalValidity(**kwargs)
    violations = tv.validate()
    if violations:
        raise ValueError(f"Temporal validity violations: {violations}")
    payload.update(tv.to_dict())
    return payload


def validate_temporal_root(payload: dict) -> list[str]:
    """Validate temporal fields in a payload. Returns violations."""
    if "valid_from" not in payload:
        return ["Missing required field: valid_from"]
    if "known_at" not in payload:
        return ["Missing required field: known_at"]

    tv = TemporalValidity(
        valid_from=payload.get("valid_from"),
        valid_until=payload.get("valid_until"),
        superseded_by=payload.get("superseded_by"),
        known_at=payload.get("known_at"),
        causal_predecessor=payload.get("causal_predecessor"),
    )
    return tv.validate()


def compute_prediction_error(observed: float, predicted: float) -> dict:
    """Compute prediction error with temporal context.

    Prediction_Error = Observed_Future - Predicted_Future
    """
    error = observed - predicted
    return {
        "observed": observed,
        "predicted": predicted,
        "error": error,
        "abs_error": abs(error),
        "pct_error": abs(error) / abs(predicted) * 100 if predicted != 0 else None,
        "direction": "over" if error > 0 else "under" if error < 0 else "exact",
        "computed_at": _now_iso(),
    }


# ───────────────────────── HELPERS ─────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime:
    """Parse ISO-8601 timestamp to datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


# ───────────────────────── EXPORTS ─────────────────────────

__all__ = [
    "TemporalValidity",
    "CausalOrder",
    "TemporalCommitment",
    "inject_temporal_root",
    "validate_temporal_root",
    "compute_prediction_error",
    "TEMPORAL_ROOT_SCHEMA",
]
