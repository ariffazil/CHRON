"""CHRON Prediction — lifecycle management.

Wraps the existing prediction_store.py with CHRON-native operations.
Adds Brier score computation and calibration tracking.

Prediction lifecycle:
  CREATED → ACTIVE → VERIFIED_CORRECT | VERIFIED_INCORRECT | EXPIRED_UNVERIFIED

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

PREDICTIONS_FILE = Path("/root/chron/data/predictions.jsonl")
CALIBRATION_FILE = Path("/root/chron/data/calibration.json")


def _emit_ariflow_predict(pred: dict) -> None:
    """Emit prediction creation as arifFlow receipt."""
    try:
        import urllib.request
        import uuid as _uuid

        receipt = {
            "receipt_id": str(_uuid.uuid4()),
            "created_at": _now_iso(),
            "actor_id": "chron",
            "session_id": f"chron-predict-{pred.get('prediction_id', 'unknown')[:12]}",
            "step_type": "Execute",
            "step_number": 0,
            "cost_ns": 0,
            "epistemic_label": "Derivation",
            "floor_verdict": "Pass",
            "cooling_decision": "None",
            "summary": (
                f"CHRON predict: {pred.get('claim', '?')[:60]} "
                f"conf={pred.get('confidence', '?')} "
                f"verify_at={pred.get('verify_at', '?')}"
            ),
            "routed_organ": "chron",
            "payload": {
                "prediction_id": pred.get("prediction_id"),
                "claim": pred.get("claim"),
                "confidence": pred.get("confidence"),
                "verify_at": pred.get("verify_at"),
            },
        }

        req = urllib.request.Request(
            "http://127.0.0.1:7073/ingest",
            data=json.dumps(receipt).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass  # arifFlow down → CHRON still works


MYT = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── PREDICTION STORE ─────────────────────────


def load_predictions() -> list[dict]:
    """Load all predictions from JSONL store."""
    if not PREDICTIONS_FILE.exists():
        return []
    predictions = []
    with open(PREDICTIONS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    predictions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return predictions


def save_prediction(prediction: dict) -> None:
    """Append a single prediction to JSONL store."""
    PREDICTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREDICTIONS_FILE, "a") as f:
        f.write(json.dumps(prediction, default=str) + "\n")


def create_prediction(
    claim: str,
    expected_outcome: str,
    confidence: float,
    verify_at: str,
    assumptions: Optional[list[str]] = None,
    source: str = "chron_events",
    source_id: Optional[str] = None,
    principal: str = "arif",
    horizon: str = "?",
    # ── P0 PROVENANCE FIELDS (added 2026-09-18 per witness report) ──
    evidence_snapshot: Optional[list[dict]] = None,
    source_refs: Optional[list[str]] = None,
    world_state_hash: Optional[str] = None,
    model: str = "chron_default",
    success_rule: str = "",
    falsification_rule: str = "",
    created_by: str = "system",
    trigger: str = "manual",
    loop_id: Optional[str] = None,
    audience: str = "both",
    **extra,
) -> dict:
    """Create a new prediction object.

    INVARIANT (F2/F11, witness-flagged 2026-09-18):
        The prediction record created here is IMMUTABLE at birth.
        Verification produces a SEPARATE record that references prediction_id.
        The original belief snapshot must survive any post-hoc rewrite.

    Required at birth (all captured together, atomically):
        prediction_id, claim, expected_outcome, confidence, verify_at,
        assumptions, evidence_snapshot, source_refs, world_state_hash,
        model, success_rule, falsification_rule, created_by, trigger,
        loop_id, created_at.

    Verification (separate record, never overwrites the above):
        observed_outcome, outcome_evidence, observed_at, verdict,
        brier_score, error_type, surprise, lesson_id, verified_at.
    """
    pred = {
        # ── Identity (immutable) ──
        "prediction_id": f"pred-{uuid.uuid4().hex[:12]}",
        "claim": claim,
        "expected_outcome": expected_outcome,
        "confidence": float(confidence),
        "verify_at": verify_at,
        "horizon": horizon,
        # ── Reasoning snapshot (immutable) ──
        "assumptions": assumptions or [],
        "evidence_snapshot": evidence_snapshot or [],
        "source_refs": source_refs or [],
        "world_state_hash": world_state_hash,
        "success_rule": success_rule,
        "falsification_rule": falsification_rule,
        # ── Provenance (immutable) ──
        "source": source,
        "source_id": source_id,
        "principal": principal,
        "audience": audience,
        "model": model,
        "created_by": created_by,
        "trigger": trigger,
        "loop_id": loop_id,
        # ── Lifecycle (mutable until verified, then frozen) ──
        # Bind-before-attention (2026-09-20): a claim with no machine probe
        # and no explicit human-witness audience is UNBOUND, not ACTIVE.
        # Cron cannot close a door that was never attached.
        "status": (
            "ACTIVE"
            if (extra.get("probe_id") or audience == "arif")
            else "UNBOUND"
        ),
        "binding": extra.get("probe_id")
        or ("HUMAN_WITNESS" if audience == "arif" else "NO_MACHINE_PROBE"),
        "created_at": _now_iso(),
        # ── Verification payload (written by verify, never by create) ──
        "observed_outcome": None,
        "outcome_evidence": None,
        "observed_at": None,
        "verdict": None,
        "error": None,
        "error_type": None,
        "surprise": None,
        "verified_at": None,
        "brier_score": None,
        "lesson_id": None,
        "supersedes": None,
    }
    pred.update(extra)
    save_prediction(pred)

    # Emit to arifFlow (makes prediction visible to federation)
    _emit_ariflow_predict(pred)

    # Create predict episode (fixes counter mismatch: episodes ↔ predictions)
    try:
        from chron.chron_store import get_store
        from chron.chron_episode import predict_from_prediction

        ep = predict_from_prediction(pred)
        get_store().append(ep)
    except Exception:
        pass  # episode creation failure → prediction still saved

    return pred


VERIFICATION_LOG = Path("/root/chron/data/verification_log.jsonl")

# Canonical verdict vocabulary. Verification records are append-only and have
# drifted across schemas, so reads normalise here instead of trusting the file.
VERDICT_CORRECT = "VERIFIED_CORRECT"
VERDICT_INCORRECT = "VERIFIED_INCORRECT"
VERDICT_UNVERIFIABLE = "UNVERIFIABLE"
VERDICT_VOID = "VOID"
V_ACTIVE = "ACTIVE"

_DECISIVE = (VERDICT_CORRECT, VERDICT_INCORRECT)


def _canonical_verdict(rec: dict) -> str:
    """Normalise a verification record's outcome to one canonical verdict.

    Schema drift is real in this log (2026-09-18 repair):
      - canonical records carry ``verdict`` ∈ {CORRECT, INCORRECT, ...}
      - legacy records carry ``status`` ∈ {VERIFIED_CORRECT, UNVERIFIABLE, ...}
      - a VOID record supersedes an earlier record for the same prediction.
    Reading both spellings here is what stops the join from silently
    reporting 0% accuracy on a ledger that is half correct.
    """
    raw = rec.get("verdict") or rec.get("status") or ""
    raw = str(raw).strip().upper()
    if raw in ("CORRECT", VERDICT_CORRECT):
        return VERDICT_CORRECT
    if raw in ("INCORRECT", VERDICT_INCORRECT):
        return VERDICT_INCORRECT
    if raw in (VERDICT_VOID, "VOIDED", "RETRACTED"):
        return VERDICT_VOID
    if raw in (VERDICT_UNVERIFIABLE, "PENDING", "NOT_YET_DUE", "EXPIRED_UNVERIFIED"):
        return VERDICT_UNVERIFIABLE
    return VERDICT_UNVERIFIABLE


def load_verifications() -> dict[str, dict]:
    """Latest verification record per prediction_id (last write wins).

    Last-write-wins is deliberate: appending a VOID record is how a bad
    verification is retracted without rewriting history.
    """
    if not VERIFICATION_LOG.exists():
        return {}
    by_pred: dict[str, dict] = {}
    with open(VERIFICATION_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = rec.get("prediction_id")
            if pid:
                by_pred[pid] = rec
    return by_pred


def decided_ids() -> set[str]:
    """Prediction IDs that carry a decisive (CORRECT/INCORRECT) verdict."""
    return {
        pid
        for pid, rec in load_verifications().items()
        if _canonical_verdict(rec) in _DECISIVE
    }


def get_active() -> list[dict]:
    """Get predictions still awaiting a decisive verdict.

    A prediction with a CORRECT/INCORRECT verification is no longer active,
    even though its immutable birth record still says ``status: ACTIVE``.
    An UNVERIFIABLE record does NOT resolve a prediction — it stays active.
    """
    decided = decided_ids()
    return [
        p
        for p in load_predictions()
        if p["status"] == V_ACTIVE and p["prediction_id"] not in decided
    ]


def get_due() -> list[dict]:
    """Get predictions whose verify_at has arrived and are still unresolved."""
    now = datetime.now(timezone.utc)
    due = []
    for p in get_active():
        try:
            verify_at = datetime.fromisoformat(p["verify_at"].replace("Z", "+00:00"))
            if now >= verify_at:
                due.append(p)
        except Exception:
            pass
    return due


def get_verified(limit: int = 50, include_void: bool = False) -> list[dict]:
    """Joined view: birth snapshot + verification outcome.

    Post-2026-09-18: predictions are IMMUTABLE at birth. Verification results
    live in verification_log.jsonl. We JOIN the two to return a unified view
    that includes both the birth snapshot AND the verification outcome.

    Reads normalise across verification-log schema drift and drop VOID
    (retracted) records, so calibration is computed over live verdicts only.
    """
    joined = []
    preds_by_id = {p["prediction_id"]: p for p in load_predictions()}

    for pred_id, verif in load_verifications().items():
        verdict = _canonical_verdict(verif)
        if verdict == VERDICT_VOID and not include_void:
            continue
        birth = preds_by_id.get(pred_id, {})
        joined.append(
            {
                **birth,
                "prediction_id": pred_id,
                "status": verdict,
                "verdict": verdict,
                "orphan": not birth,
                "verified_at": verif.get("verified_at"),
                "observed_outcome": verif.get("observed_outcome"),
                "brier_score": verif.get("brier_score"),
                "error": verif.get("error"),
                "error_type": verif.get("error_type") or (
                    "UNKNOWN" if verdict == VERDICT_UNVERIFIABLE else "NONE"
                ),
            }
        )

    joined.sort(key=lambda p: p.get("verified_at") or "", reverse=True)
    return joined[:limit]


# ───────────────────────── BRIER SCORE ─────────────────────────


def compute_brier(prediction: dict, outcome: bool) -> float:
    """Compute Brier score for a binary prediction.

    Brier = (confidence - outcome)^2
    where outcome = 1 if correct, 0 if incorrect.

    Lower is better. Perfect prediction = 0.0.
    Always predicting 0.5 = 0.25.
    """
    confidence = prediction.get("confidence", 0.5)
    return (confidence - (1.0 if outcome else 0.0)) ** 2


def verify_prediction(
    prediction: dict, observed_outcome: str, correct: bool, unverifiable: bool = False
) -> dict:
    """Verify a prediction and compute Brier score.

    INVARIANT (F2/F11, witness-flagged 2026-09-18):
        This function does NOT mutate the original prediction record.
        It returns a SEPARATE verification record that references prediction_id.
        The original belief snapshot is preserved.

    INVARIANT (2026-09-18 repair — no-claim-from-silence):
        ``unverifiable=True`` records that verification was ATTEMPTED and
        found no evidence. It emits verdict UNVERIFIABLE with no error and
        no Brier score. Silence must never be converted into a verdict —
        a missed check is not a missed prediction.

    The caller is responsible for:
        1. Writing this verification record to verification_log.jsonl
        2. NOT rewriting the original prediction in predictions.jsonl
    """
    if unverifiable:
        verdict = VERDICT_UNVERIFIABLE
        error = None
        error_type = "AWAITING_EVIDENCE"
        brier = None
    else:
        verdict = VERDICT_CORRECT if correct else VERDICT_INCORRECT
        error = 0.0 if correct else 1.0
        error_type = "NONE" if correct else _classify_error(prediction)
        brier = compute_brier(prediction, correct)

    return {
        "verification_id": f"verif-{uuid.uuid4().hex[:12]}",
        "prediction_id": prediction["prediction_id"],
        "claim_at_birth": prediction["claim"],
        "confidence_at_birth": prediction["confidence"],
        "expected_outcome_at_birth": prediction["expected_outcome"],
        "assumptions_at_birth": prediction.get("assumptions", []),
        "evidence_snapshot_at_birth": prediction.get("evidence_snapshot", []),
        "source_refs_at_birth": prediction.get("source_refs", []),
        "world_state_hash_at_birth": prediction.get("world_state_hash"),
        "model_at_birth": prediction.get("model", "unknown"),
        "observed_outcome": observed_outcome,
        "observed_at": _now_iso(),
        "verdict": verdict,
        "error": error,
        "error_type": error_type,
        "brier_score": brier,
        "verified_at": _now_iso(),
    }


def _classify_error(prediction: dict) -> str:
    """Classify why a prediction was wrong."""
    if not prediction.get("assumptions"):
        return "UNKNOWN"
    # Default — more specific classification needs domain context
    return "ASSUMPTION_ERROR"


# ───────────────────────── CALIBRATION ─────────────────────────


def compute_calibration() -> dict:
    """Compute calibration statistics over decided predictions.

    Accounting rules (2026-09-18 repair — this is a truth surface):
      - Only DECISIVE verdicts (CORRECT / INCORRECT) enter the ratio.
        An UNVERIFIABLE record is absence of evidence, not evidence of
        a miss; counting it as 'incorrect' would manufacture a 0% figure
        out of a ledger that is partly correct.
      - ORPHAN records (verification with no matching birth prediction)
        are reported but never scored.
      - VOID (retracted) records are excluded upstream by get_verified().

    Evidence / pre-change snapshot:
      /root/chron/.backup-chron-repair-20260918T152417Z/calibration.json
    """
    verified = get_verified(limit=10000)
    scored = [
        p for p in verified if p.get("verdict") in _DECISIVE and not p.get("orphan")
    ]
    orphans = [p for p in verified if p.get("orphan")]
    unverifiable = [p for p in verified if p.get("verdict") == VERDICT_UNVERIFIABLE]

    correct = sum(1 for p in scored if p.get("verdict") == VERDICT_CORRECT)
    incorrect = sum(1 for p in scored if p.get("verdict") == VERDICT_INCORRECT)

    brier_scores: list[float] = []
    for p in scored:
        bs = p.get("brier_score")
        if bs is not None:
            brier_scores.append(float(bs))

    by_error_type: dict[str, int] = {}
    for p in scored:
        et = p.get("error_type") or "UNKNOWN"
        by_error_type[et] = by_error_type.get(et, 0) + 1

    return {
        # ── Backward-compatible keys (server.py health + MCP read these) ──
        "total": len(scored),
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": (correct / len(scored)) if scored else None,
        "mean_brier": (sum(brier_scores) / len(brier_scores)) if brier_scores else None,
        "by_error_type": by_error_type,
        # ── Disclosed detail: what was NOT scored, and why ──
        "decisive": len(scored),
        "unverifiable": len(unverifiable),
        "orphan_records": len(orphans),
        "records_in_log": len(verified),
    }


def save_calibration(calibration: dict) -> None:
    """Save calibration stats."""
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    calibration["updated_at"] = _now_iso()
    CALIBRATION_FILE.write_text(json.dumps(calibration, indent=2, default=str))


def load_calibration() -> Optional[dict]:
    """Load calibration stats."""
    if not CALIBRATION_FILE.exists():
        return None
    try:
        return json.loads(CALIBRATION_FILE.read_text())
    except Exception:
        return None


# ───────────────────────── GENERATE FROM EVENTS ─────────────────────────


def generate_from_chron_events() -> list[dict]:
    """Generate falsifiable predictions from chron_events.json.

    Reads the `predictions[]` array on each event. Each entry carries:
    claim, expected_value, threshold, verifier, falsifier, confidence.
    Falls back to trivial "event will occur" for events without predictions[].
    """
    chron_events = Path("/root/AAA/scripts/chron_events.json")
    if not chron_events.exists():
        return []

    data = json.loads(chron_events.read_text())
    events = data.get("events", [])
    today = datetime.now(MYT).date()

    # Track existing source_ids to avoid duplicates (by event id + claim hash)
    existing_keys = set()
    for p in load_predictions():
        sid = p.get("source_id", "")
        claim = p.get("claim", "")
        existing_keys.add(f"{sid}||{claim}")

    predictions = []
    for event in events:
        event_id = event.get("id", "")
        target_date_str = event.get("target_date")
        if not target_date_str:
            continue

        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        days_until = (target_date - today).days
        if days_until < 0:
            continue

        horizon_days = days_until
        if horizon_days <= 7:
            horizon = f"{horizon_days}d"
        elif horizon_days <= 30:
            horizon = f"{horizon_days // 7}w"
        else:
            horizon = f"{horizon_days // 30}m"

        verify_at = f"{target_date_str}T23:59:59+08:00"
        title = event.get("title", "?")

        # ── Falsifiable predictions from event.predictions[] ──
        event_preds = event.get("predictions", [])
        if event_preds:
            for sub_pred in event_preds:
                claim = sub_pred.get("claim", "")
                dedup_key = f"{event_id}||{claim}"
                if dedup_key in existing_keys:
                    continue

                # ── P0 provenance capture: snapshot the reasoning at birth ──
                event_source = event.get("source", "")
                evidence_snapshot = [
                    {
                        "evidence_class": "EVENT_REFERENCE",
                        "source_ref": event_source,
                        "event_id": event_id,
                        "event_confidence": event.get("confidence", ""),
                        "kind": event.get("kind", ""),
                    }
                ]
                source_refs = [event_source] if event_source else []
                success_rule = sub_pred.get("verifier", "")
                falsification_rule = sub_pred.get("falsifier", "")

                pred = create_prediction(
                    claim=claim,
                    expected_outcome=sub_pred.get("expected_value", claim),
                    confidence=sub_pred.get("confidence", 0.5),
                    verify_at=verify_at,
                    assumptions=sub_pred.get("assumptions", []),
                    source="chron_events",
                    source_id=event_id,
                    horizon=horizon,
                    audience=event.get("audience", "both"),
                    falsifier=falsification_rule,
                    verifier_method=success_rule,
                    probe_id=sub_pred.get("probe_id"),
                    threshold=sub_pred.get("threshold"),
                    threshold_low=sub_pred.get("threshold_low"),
                    threshold_high=sub_pred.get("threshold_high"),
                    unit=sub_pred.get("unit", ""),
                    # ── P0 provenance fields ──
                    evidence_snapshot=evidence_snapshot,
                    source_refs=source_refs,
                    success_rule=success_rule,
                    falsification_rule=falsification_rule,
                    created_by="chron_prediction.generate_from_chron_events",
                    trigger="manual_generate",
                    loop_id=None,
                )
                predictions.append(pred)
            continue

        # ── Fallback: trivial "event will occur" (legacy events without predictions[]) ──
        dedup_key = f"{event_id}||Event: {title}"
        if dedup_key in existing_keys:
            continue

        confidence_map = {
            "CONFIRMED": 0.9,
            "LIKELY": 0.7,
            "ANNOUNCED": 0.8,
            "TENTATIVE": 0.6,
        }
        confidence = confidence_map.get(event.get("confidence", ""), 0.5)

        pred = create_prediction(
            claim=f"Event: {title}",
            expected_outcome=f"{title} will occur by {target_date_str}",
            confidence=confidence,
            verify_at=verify_at,
            assumptions=[],
            source="chron_events",
            source_id=event_id,
            horizon=horizon,
            # ── P0 provenance fields ──
            evidence_snapshot=[
                {
                    "evidence_class": "EVENT_REFERENCE",
                    "source_ref": event.get("source", ""),
                    "event_id": event_id,
                    "event_confidence": event.get("confidence", ""),
                    "kind": event.get("kind", ""),
                }
            ],
            source_refs=[event.get("source", "")] if event.get("source") else [],
            success_rule=f"Event '{title}' occurs by {target_date_str}",
            falsification_rule=f"Event '{title}' does not occur by {target_date_str}",
            created_by="chron_prediction.generate_from_chron_events",
            trigger="manual_generate",
            loop_id=None,
        )
        predictions.append(pred)

    return predictions
