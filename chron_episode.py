"""CHRON Episode Factory — creates ChronEpisode objects from real data.

Converts:
  - chron_events.json → observe episodes
  - predictions → predict episodes
  - verification results → verify episodes
  - lessons → learn episodes

Each episode follows CHRON-EPISODE-SCHEMA-v1.json.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

MYT = timezone(timedelta(hours=8))
CHRON_EVENTS = Path("/root/AAA/scripts/chron_events.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_id(function: str, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, default=str).encode()
    import hashlib

    h = hashlib.sha256(canonical).hexdigest()[:8]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"chron-ep-{today}-{function}-{h}"


# ───────────────────────── OBSERVE EPISODE ─────────────────────────


def observe_from_event(event: dict) -> dict:
    """Create an observe episode from a chron_events.json entry."""
    now = _now_iso()
    target = event.get("target_date", "")
    valid_time = f"{target}T00:00:00+08:00" if target else now

    body = {
        "function": "observe",
        "question": "What materially changed?",
        "principal": event.get("audience", "both"),
        "audience": "shared" if event.get("audience") == "both" else "private",
        "valid_time": valid_time,
        "observed_at": now,
        "observations": [
            {
                "obs_id": f"obs-{event.get('id', '?')}",
                "source": f"chron_events:{event.get('id', '?')}",
                "content": event.get("title", "?"),
                "observed_at": now,
                "truth_class": "OBS"
                if event.get("confidence") == "CONFIRMED"
                else "INT",
                "confidence": {
                    "CONFIRMED": 0.95,
                    "ANNOUNCED": 0.8,
                    "TENTATIVE": 0.6,
                }.get(event.get("confidence", "CONFIRMED"), 0.7),
            }
        ],
        "claims": [
            {
                "claim_id": f"claim-{event.get('id', '?')}",
                "statement": f"{event.get('title', '?')} will occur on {target}",
                "truth_state": "REPORTED",
                "asserted_at": now,
                "asserted_by": event.get("source", "unknown"),
                "evidence_refs": [],
            }
        ],
        "selected_signals": [
            {
                "signal_id": f"sig-{event.get('id', '?')}",
                "domain": {
                    "FISCAL": "MONEY",
                    "MARKET_EVENT": "MONEY",
                    "REGULATORY_WINDOW": "REALITY",
                    "PERSONAL_SAFE": "HUMAN",
                }.get(event.get("kind", ""), "REALITY"),
                "content": event.get("title", "?"),
                "ranking_score": 0.0,  # computed by chron.py
                "truth_score": 0.0,
                "urgency_score": 0.0,
                "consequence": event.get("consequence", "MEDIUM"),
                "actionability": event.get("actionability", "WATCH"),
                "audience": event.get("audience", "both"),
            }
        ],
        "rejected_signals": [],
        "predictions": [],
        "decisions": [],
        "actions": [],
        "outcomes": [],
        "output_decision": "SILENT",
        "renderer_used": "none",
        "provenance": [
            {
                "source_kind": "EXTERNAL_API",
                "source_ref": event.get("source", "unknown"),
                "retrieved_at": now,
                "method": "chron_events.json ingestion",
                "evidence_class": "PRIMARY",
            }
        ],
        "receipts": [],
    }

    body["episode_id"] = _make_id("observe", body)
    return body


def observe_all_events() -> list[dict]:
    """Create observe episodes for all chron_events."""
    if not CHRON_EVENTS.exists():
        return []
    data = json.loads(CHRON_EVENTS.read_text())
    events = data.get("events", [])
    return [observe_from_event(e) for e in events]


# ───────────────────────── PREDICT EPISODE ─────────────────────────


def predict_from_prediction(prediction: dict) -> dict:
    """Create a predict episode from a prediction object."""
    now = _now_iso()

    body = {
        "function": "predict",
        "question": "What do we expect next?",
        "principal": prediction.get("principal", "arif"),
        "audience": "private",
        "valid_time": prediction.get("verify_at", now),
        "observed_at": now,
        "observations": [],
        "claims": [],
        "selected_signals": [],
        "rejected_signals": [],
        "predictions": [
            {
                "prediction_id": prediction.get(
                    "prediction_id", f"pred-{uuid.uuid4().hex[:12]}"
                ),
                "claim": prediction.get("claim", "?"),
                "confidence": prediction.get("confidence", 0.5),
                "horizon": prediction.get("horizon", "?"),
                "verify_at": prediction.get("verify_at", now),
                "assumptions": prediction.get("assumptions", []),
                "evidence": prediction.get("evidence", []),
                "principal": prediction.get("principal", "arif"),
                "status": "PENDING",
                "verified_at": None,
                "verification_outcome_ref": None,
                "brier_score": None,
            }
        ],
        "decisions": [],
        "actions": [],
        "outcomes": [],
        "output_decision": "SILENT",
        "renderer_used": "none",
        "provenance": [
            {
                "source_kind": "COMPUTATION",
                "source_ref": f"prediction_store:{prediction.get('prediction_id', '?')}",
                "retrieved_at": now,
                "method": "chron_prediction.generate_from_chron_events",
                "evidence_class": "INFERRED",
            }
        ],
        "receipts": [],
    }

    body["episode_id"] = _make_id("predict", body)
    return body


# ───────────────────────── VERIFY EPISODE ─────────────────────────


def verify_from_result(
    prediction: dict,
    observed_outcome: str,
    error: Optional[float],
    error_class: str,
) -> dict:
    """Create a verify episode after checking a prediction against reality."""
    now = _now_iso()

    body = {
        "function": "verify",
        "question": "Were we right?",
        "principal": prediction.get("principal", "arif"),
        "audience": "private",
        "valid_time": now,
        "observed_at": now,
        "observations": [
            {
                "obs_id": f"obs-verify-{prediction.get('prediction_id', '?')}",
                "source": "prediction_verifier",
                "content": observed_outcome,
                "observed_at": now,
                "truth_class": "OBS",
                "confidence": 0.9,
            }
        ],
        "claims": [],
        "selected_signals": [],
        "rejected_signals": [],
        "predictions": [],
        "decisions": [],
        "actions": [],
        "outcomes": [
            {
                "outcome_id": f"out-{uuid.uuid4().hex[:8]}",
                "description": f"Verification of: {prediction.get('claim', '?')}",
                "observed_at": now,
                "prediction_ref": prediction.get("prediction_id", "?"),
                "expected": prediction.get("expected_outcome", "?"),
                "observed": observed_outcome,
                "error_delta": str(error) if error is not None else "UNKNOWN",
                "error_class": error_class,
            }
        ],
        "output_decision": "SILENT",
        "renderer_used": "none",
        "provenance": [
            {
                "source_kind": "COMPUTATION",
                "source_ref": "chron_verify",
                "retrieved_at": now,
                "method": "prediction_verifier.verify_event_prediction",
                "evidence_class": "PRIMARY",
            }
        ],
        "receipts": [],
        "parent_episode_ids": [],  # linked via prediction_ref
    }

    body["episode_id"] = _make_id("verify", body)
    return body


# ───────────────────────── LEARN EPISODE ─────────────────────────


def learn_from_error(
    prediction: dict,
    error_class: str,
    lesson: str,
    brier_score: float,
) -> dict:
    """Create a learn episode after extracting a lesson from a verified prediction."""
    now = _now_iso()

    body = {
        "function": "learn",
        "question": "Which error deserves to survive?",
        "principal": prediction.get("principal", "arif"),
        "audience": "private",
        "valid_time": now,
        "observed_at": now,
        "observations": [],
        "claims": [
            {
                "claim_id": f"claim-learn-{uuid.uuid4().hex[:8]}",
                "statement": lesson,
                "truth_state": "INFERRED",
                "asserted_at": now,
                "asserted_by": "chron_learn",
                "basis": f"Verified prediction {prediction.get('prediction_id', '?')} "
                f"with error_class={error_class}, brier={brier_score:.3f}",
                "evidence_refs": [],
            }
        ],
        "selected_signals": [],
        "rejected_signals": [],
        "predictions": [],
        "decisions": [],
        "actions": [],
        "outcomes": [],
        "output_decision": "SILENT",
        "renderer_used": "none",
        "provenance": [
            {
                "source_kind": "COMPUTATION",
                "source_ref": "chron_learn",
                "retrieved_at": now,
                "method": "lesson_extraction",
                "evidence_class": "INFERRED",
            }
        ],
        "receipts": [],
    }

    body["episode_id"] = _make_id("learn", body)
    return body
