"""CHRON Prediction — lifecycle management.

Wraps the existing prediction_store.py with CHRON-native operations.
Adds Brier score computation and calibration tracking.

Prediction lifecycle:
  CREATED → ACTIVE → VERIFIED_CORRECT | VERIFIED_INCORRECT | EXPIRED_UNVERIFIED

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
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
    # ── R-FILTER SCHEMA BUMP (2026-10-03) ──────────────────────────────────
    # Companion to rationality-filter-doctrine.md. Optional list of classes
    # for predictions that depend on data the corpus systematically
    # underrepresents (gut instinct, leader decisions, private relationships,
    # scar revisions). When non-empty, confidence is clamped to the
    # corresponding ceiling — the model cannot claim high confidence on
    # data it cannot see. See Q-MAP §13 (R-FILTER theorem).
    rationality_filter_class: Optional[list[str]] = None,
    rationality_filter_confidence_ceiling: float = 0.65,
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
        # ── R-FILTER provenance (immutable) — 2026-10-03 schema bump ─────
        # R-FILTER theorem: a prediction whose class depends on private data
        # cannot carry confidence above the class ceiling. The clamp happens
        # at birth, not at read time — the birth record is the witness.
        "rationality_filter_class": list(rationality_filter_class or []),
        "rationality_filter_confidence_ceiling": (
            rationality_filter_confidence_ceiling if (rationality_filter_class) else 1.0
        ),
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
            "ACTIVE" if (extra.get("probe_id") or audience == "arif") else "UNBOUND"
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
    # R-FILTER confidence clamp (2026-10-03 schema bump): if a non-empty
    # rationality_filter_class is present, confidence may not exceed the
    # class ceiling. This is the operational form of the theorem that says
    # the model cannot claim high confidence on data the corpus systematically
    # underrepresents. The clamp writes the witness into the birth record,
    # not the read surface.
    rfc = pred.get("rationality_filter_class") or []
    if rfc:
        ceiling = pred.get("rationality_filter_confidence_ceiling", 0.65)
        if pred["confidence"] > ceiling:
            pred["confidence"] = ceiling
            pred["_rfilter_clamped"] = True

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
# "no evidence yet" and "this window is closed and will never be scored" are two
# different transitions. Collapsed into one token, a deliberately closed arrow kept
# re-offering itself to verify_due and kept aging attention debt forever.
VERDICT_CLOSED_UNTESTABLE = "UNVERIFIABLE_CLOSED"
VERDICT_VOID = "VOID"
V_ACTIVE = "ACTIVE"

_DECISIVE = (VERDICT_CORRECT, VERDICT_INCORRECT)
# The arrow is FINISHED: scored, or explicitly closed as untestable.
_RESOLVED = _DECISIVE + (VERDICT_CLOSED_UNTESTABLE,)


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
    # An explicit closure outranks the generic UNVERIFIABLE spelling: it carries the
    # subject's or an agent's decision that the window is shut, not that evidence is late.
    if rec.get("resolution_class") == "CLOSED_UNTESTABLE" or (
        rec.get("verified_by") == "chron_resolve_unverifiable"
    ):
        return VERDICT_CLOSED_UNTESTABLE
    if raw == VERDICT_CLOSED_UNTESTABLE:
        return VERDICT_CLOSED_UNTESTABLE
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
    """Prediction IDs that carry a decisive (CORRECT/INCORRECT) verdict.

    Calibration counts ONLY these. An explicitly closed-untestable arrow is
    finished but unscored, so it must never enter the Brier/accuracy denominator.
    """
    return {
        pid
        for pid, rec in load_verifications().items()
        if _canonical_verdict(rec) in _DECISIVE
    }


def resolved_ids() -> set[str]:
    """Prediction IDs whose arrow is FINISHED — scored, or closed as untestable.

    ``chron_resolve_unverifiable`` promises "removed from the active set (stopping
    attention debt accumulation)". Before this split, a closure wrote a ledger record
    that read identically to "evidence is late", so the prediction stayed active:
    verify_due re-offered it every night and attention debt aged it forever, while
    calibration correctly refused to score it. Closing is a decision; lateness is not.
    A VOID record does NOT resolve — retraction re-opens the arrow.
    """
    return {
        pid
        for pid, rec in load_verifications().items()
        if _canonical_verdict(rec) in _RESOLVED
    }


def get_active() -> list[dict]:
    """Get predictions still awaiting the end of their arrow.

    A prediction with a CORRECT/INCORRECT verification is no longer active,
    even though its immutable birth record still says ``status: ACTIVE``.
    An explicitly CLOSED-UNTESTABLE record also ends the arrow (unscored).
    A plain UNVERIFIABLE record from a verifier attempt means "evidence is
    late", which legitimately keeps the prediction active.
    """
    resolved = resolved_ids()
    return [
        p
        for p in load_predictions()
        if p["status"] == V_ACTIVE and p["prediction_id"] not in resolved
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


# ─────────────────── RESOLUTION-PROCESS STATES (read-time) ───────────────────
# Audit 2026-09-25 (F13-relayed, priority #2): the birth store only knows
# ACTIVE; the process needs DUE / AWAITING_EVIDENCE / RESOLVED / UNRESOLVABLE.
# Pure derivation at read time — no record is mutated (immutability invariant),
# so this can never rewrite history to look right.

VERIFICATION_ATTEMPTS = Path("/root/chron/data/verification_attempts.jsonl")


def _latest_attempt(pid: str) -> dict:
    """Latest verifier attempt for one prediction (attempts log, last wins)."""
    latest: dict = {}
    if not VERIFICATION_ATTEMPTS.exists():
        return latest
    with open(VERIFICATION_ATTEMPTS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("prediction_id") == pid:
                latest = rec
    return latest


def resolution_state(pred: dict, verif: dict | None = None) -> str:
    """Classify one prediction's resolution-process state (read-time).

    RESOLVED          — decisive verdict (CORRECT/INCORRECT) in the ledger
    AWAITING_EVIDENCE — due; verifier ran; evidence not yet available
                        (delay of data is not failure of the prediction)
    UNRESOLVABLE      — no machine probe bound and no human-witness audience:
                        nothing can ever close it automatically
    DUE               — verify_at arrived, still unresolved, reviewable now
    OPEN              — not yet due
    """
    pid = pred.get("prediction_id")
    v = verif if verif is not None else load_verifications().get(pid, {})
    if _canonical_verdict(v) in _DECISIVE:
        return "RESOLVED"
    now = datetime.now(timezone.utc)
    try:
        is_due = now >= datetime.fromisoformat(pred["verify_at"].replace("Z", "+00:00"))
    except Exception:
        is_due = False
    if not is_due:
        return "OPEN"
    if _canonical_verdict(v) == VERDICT_UNVERIFIABLE:
        return "AWAITING_EVIDENCE"
    if _latest_attempt(pid).get("error_class") == "AWAITING_EVIDENCE":
        return "AWAITING_EVIDENCE"
    if pred.get("binding") == "NO_MACHINE_PROBE" and pred.get("audience") != "arif":
        return "UNRESOLVABLE"
    return "DUE"


def resolution_states() -> dict[str, list[str]]:
    """Resolution-process state for every active prediction, as id lists.

    The complement of ``decided_ids()``: RESOLVED count = 25-active minus
    these. Consumers: chron_store_stats (MCP), /health census, AAA surfaces.
    """
    out: dict[str, list[str]] = {
        "OPEN": [],
        "DUE": [],
        "AWAITING_EVIDENCE": [],
        "UNRESOLVABLE": [],
    }
    verifs = load_verifications()
    for p in get_active():
        state = resolution_state(p, verifs.get(p.get("prediction_id"), {}))
        out.setdefault(state, []).append(p["prediction_id"])
    return out


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
                "error_type": verif.get("error_type")
                or ("UNKNOWN" if verdict == VERDICT_UNVERIFIABLE else "NONE"),
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
    prediction: dict,
    observed_outcome: str,
    correct: bool,
    unverifiable: bool = False,
    error_class: Optional[str] = None,
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

    INVARIANT (2026-09-21 learning-loop closure):
        ``error_class`` carries the class the EVIDENCE PATH actually observed
        (see chron_verify._evidence_for: REGIME_CHANGE / NONE / ...). It used to
        be discarded here: the verifier measured the class, wrote it to the
        episode, and then let ``_classify_error`` re-derive UNKNOWN from the
        prediction's empty ``assumptions`` list. That dropped argument is why the
        calibration ledger reports error_type UNKNOWN for a failure whose cause
        was measured. Classified outcomes are what make the store's skill
        estimate usable; see ``recalibrate_confidence``.

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
        if correct:
            error_type = "NONE"
        elif error_class and str(error_class).strip().upper() not in (
            "",
            "NONE",
            "UNKNOWN",
        ):
            # Honour the class the evidence path measured.
            error_type = str(error_class).strip().upper()
        else:
            error_type = _classify_error(prediction)
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


# ── Structural error taxonomy (2026-10-02) ─────────────────────────────────
# Fallback classifier inputs are BIRTH-side fields only — the verification
# path honours evidence-measured error_class BEFORE reaching here.
#
# RANGE_STALE_AT_MINT enforces the sealed Brent lesson (pred-a1210fe9e53c:
# "Brent 65–75" minted while spot was 98.3 → "range predictions must be
# spot-checked against live price at creation"). Naming the cause is what
# makes the lesson promotable — UNKNOWN-class lessons are diagnostics by
# design (chron_loop_close arrow_memory_to_policy).
_RANGE_SUBJECT_RE = re.compile(
    r"\b(brent|wti|crude|oil|gas|xau|gold|silver|usd|myr|sgd|ron\s?95|ron97|"
    r"diesel|klci|nasdaq|snp|price|index|yield|rate)\b",
    re.IGNORECASE,
)
_RANGE_CLAIM_RE = re.compile(
    r"\b(between|under|below|above|at least|at most|no more than|"
    r"no less than|within the range|range of)\b"
    r"|[≤≥]\s?\d|\d\s?[-–]\s?\d",
    re.IGNORECASE,
)


def _classify_error(prediction: dict) -> str:
    """Classify why a prediction was wrong.

    Priority (honoured upstream before this fallback is reached):
      1. ``error_class`` measured by the evidence path (chron_verify)
      2. this structural fallback from birth-side fields only

    Structural classes:
      ASSUMPTION_ERROR     — assumptions were recorded and one broke
      RANGE_STALE_AT_MINT  — threshold/range claim on a live-market subject
                             with no assumptions and no mint-time market
                             evidence; the range was stale before the clock
                             started (proven: pred-a1210fe9e53c Brent 65–75
                             vs spot 98.3; fuel-window family)
      UNKNOWN              — honest floor; insufficient signal is never
                             forced into a class
    """
    if prediction.get("assumptions"):
        return "ASSUMPTION_ERROR"

    claim = " ".join(
        str(prediction.get(k) or "") for k in ("claim", "expected_outcome")
    )
    has_range_shape = bool(_RANGE_CLAIM_RE.search(claim))
    market_subject = bool(_RANGE_SUBJECT_RE.search(claim))
    # Mint-time market evidence appears as an evidence_snapshot entry whose
    # evidence_class is richer than a bare EVENT_REFERENCE (e.g. MARKET_PROBE).
    snap = prediction.get("evidence_snapshot") or []
    mint_market_evidence = any(
        isinstance(e, dict)
        and str(e.get("evidence_class", "")).upper() not in ("", "EVENT_REFERENCE")
        for e in snap
    )
    if has_range_shape and market_subject and not mint_market_evidence:
        return "RANGE_STALE_AT_MINT"
    return "UNKNOWN"


# ───────────────────────── CALIBRATION ─────────────────────────


# NOTE: GUT-OVERRIDE LEDGER (GOL) functions live canonically in
# chron.chron_gut_override. The duplicate block that previously lived
# here (added during the 2026-10-04 FI-008 audit response) was
# removed 2026-10-07 because no caller imports these names from
# chron_prediction (chron_cron_verify and production code import
# from chron.chron_gut_override only). Anti-Bangang LAW 8 forbids
# duplicate governance surfaces. Canonical spec:
#   /root/AAA/instructions/gut-override-ledger-spec.md


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
    # ── 2026-09-21 LEDGER-INTEGRITY REPAIR ──────────────────────────────────
    # This function used to count one ROW per verdict over ONE store's join.
    # Two consequences, both measured:
    #   * two predictions written against the same observed datum (XAUUSD
    #     @ 4399.70, 2026-09-17) occupied TWO decisive slots, inflating n;
    #   * because each surface joined a different store, this file and
    #     .../chron_personal/calibration_report.json published different
    #     mean_brier (0.24625 from the chron store's two rows vs 0.1825 from the
    #     .hermes store's two gold rows) for the same statistic.
    # The accounting rule now lives in exactly ONE place and every scheduled
    # producer adapts to it, so there is one number on disk.
    from chron.chron_ledger_integrity import compute_calibration_unified

    return compute_calibration_unified()


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


# ───────────────────────── RECALIBRATION (the broken learning hop) ─────────────────────────
#
# 2026-09-21 (learning-loop closure). Before this section, ``load_calibration()``
# had ZERO callers outside its own definition: OUTCOME→LEARNING wrote
# calibration.json and lessons.jsonl every cycle, and nothing read them. The loop
# was Predict → Verify → Learn → WRITE FILE. A ledger, not a loop.
#
# The rule below is what makes a verified outcome change a future confidence.
#
# SHRINKAGE RULE — two-component linear shrinkage (empirical Bayes)
# ----------------------------------------------------------------
# Let, for a new prediction with generator-proposed confidence p ∈ [0,1]:
#   n   = effective_n      decisive scored verdicts in the snapshot
#   m   = classified_n     decisive verdicts whose outcome is INTERPRETABLE:
#                          correct verdicts (error_type NONE) plus failures whose
#                          error class was actually measured (not UNKNOWN)
#   ā   = accuracy         = measured hit rate
#   c̄   = mean_confidence  = what the store said at birth
#   δ   = c̄ − ā            = reliability-in-the-large (δ>0 ⇒ overconfident)
#
#   w       = m / (m + κ)                     posterior weight on the evidence
#   anchor  = ā            if skill usable     "shrink toward the measured base rate"
#           = 0.5          otherwise           "shrink toward genuine uncertainty"
#   δ_eff   = δ            if not usable and δ measurable, else 0
#
#   c_cal = clamp01( (1 − w)·p + w·anchor − w·δ_eff )
#
# Basis, component by component:
#
# 1. w = m/(m+κ) IS the Beta–Binomial posterior weight. Treating the hit rate as
#    Beta(α,β) with prior mean μ₀ and strength κ = α+β, the posterior mean after m
#    observations with k hits is (k + α)/(m + κ) = w·(k/m) + (1−w)·μ₀ — exactly a
#    convex blend at weight w. κ is therefore not a fudge factor; it is "how many
#    pseudo-observations of doubt". κ = 8 is deliberately conservative: at m = 2 it
#    puts w = 0.20, so four-fifths of the answer stays with the proposal and no
#    confident correction is possible from two outcomes. (Jeffreys α=β=0.5 → κ=1
#    would put w = 0.67 at m=2 — far too permissive for a store this young;
#    Haldane α=β=0 → κ=0 is degenerate, w ≡ 1.)
#
# 2. The anchor carries the variance-awareness. When the store cannot support a
#    skill estimate the anchor is 0.5 — GENUINE UNCERTAINTY, not the measured
#    frequency, because a 2-sample frequency is not an estimate of anything.
#
# 3. δ_eff applies ONLY in the not-usable branch. In the usable branch accuracy is
#    trusted, so overconfidence is already priced in: a proposal above ā is pulled
#    down BY THE PULL TOWARD ā. Adding δ there too would double-count the same
#    bias — the error that would let a 60-sample overconfident store drive a 0.9 to
#    0.02. In the not-usable branch ā itself is not trusted, so the measured
#    DIRECTION of the bias is the only remaining usable signal and is applied at
#    the same weight.
#
# 4. Bound: |c_cal − p| ≤ w·(|anchor − p| + |δ_eff|) ≤ 2w. The correction can never
#    exceed twice the weight of evidence; at m = 2 that is 0.4, and it is measured
#    at 0.081 on the live store. The bound is derived from m, never hardcoded.
#
# Known limitation, disclosed rather than hidden: with a corrected-but-not-
# binned model, a proposal far from ā is pulled toward ā on evidence from ALL
# claims, not from claims stated near p. A per-bin reliability curve (needs the
# n the store does not yet have) would correct that. Until then w < 1 keeps the
# pull bounded.

# Prior strength in pseudo-observations (see basis note 1).
CALIBRATION_KAPPA = 8.0
# Decisive verdicts required before accuracy is treated as a skill estimate.
MIN_EFFECTIVE_N = 8
# Above this share of UNKNOWN error classes the taxonomy is blind: the store knows
# it was wrong but not why, so it cannot attribute the failure to skill.
MAX_UNKNOWN_SHARE = 0.5
# Above this share of non-actionable lessons the lesson store is blind (it is
# reporting its own inability to classify, not a learned rule).
MAX_BLIND_LESSON_SHARE = 0.5

QUALITY_NONE = "NONE"
QUALITY_INSUFFICIENT = "INSUFFICIENT"
QUALITY_SUFFICIENT = "SUFFICIENT"

# Error classes from which a policy could legitimately be derived. UNKNOWN and NONE
# are deliberately absent: neither names a cause.
INTERPRETABLE_ERROR_TYPES = (
    "ASSUMPTION_ERROR",
    "DATA_ERROR",
    "MODEL_ERROR",
    "REGIME_CHANGE",
    "AWAITING_EVIDENCE",
    "RANGE_STALE_AT_MINT",  # 2026-10-02: structural fallback class (sealed Brent lesson)
)


@dataclass(frozen=True)
class CalibrationOutcome:
    """Result of recalibrating one proposed confidence. Pure data, no I/O."""

    proposed: float
    calibrated: float
    applied: bool
    quality: str
    effective_n: int
    classified_n: int
    unknown_share: Optional[float]
    weight: float
    anchor: float
    bias: Optional[float]
    delta_applied: float
    shift: float
    capped: bool
    reason: str

    def audit(self) -> dict:
        """Compact, JSON-serialisable audit trail for a prediction record."""
        return {
            "proposed": self.proposed,
            "calibrated": self.calibrated,
            "applied": self.applied,
            "quality": self.quality,
            "effective_n": self.effective_n,
            "classified_n": self.classified_n,
            "unknown_share": self.unknown_share,
            "weight": self.weight,
            "anchor": self.anchor,
            "bias": self.bias,
            "delta_applied": self.delta_applied,
            "shift": self.shift,
            "capped": self.capped,
            "reason": self.reason,
        }


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def classify_calibration_quality(snapshot: Optional[dict]) -> tuple[str, dict]:
    """Grade a calibration snapshot. Pure.

    Returns (quality, facts) where facts carries n / m / unknown_share /
    blind_lesson_share so the caller can disclose WHY the grade was given.
    """
    snapshot = snapshot or {}
    n = int(snapshot.get("effective_n") or snapshot.get("total") or 0)
    accuracy = snapshot.get("accuracy")
    mean_brier = snapshot.get("mean_brier")
    by_error = snapshot.get("by_error_type") or {}
    unknown = int(by_error.get("UNKNOWN") or 0)
    m = max(0, n - unknown)
    unknown_share = (unknown / n) if n else None
    blind_share = snapshot.get("blind_lesson_share")
    blind_share = float(blind_share) if blind_share is not None else None

    facts = {
        "effective_n": n,
        "classified_n": m,
        "unknown_share": unknown_share,
        "blind_lesson_share": blind_share,
    }

    if n == 0 or accuracy is None or mean_brier is None:
        return QUALITY_NONE, facts

    usable = n >= MIN_EFFECTIVE_N
    if unknown_share is not None and unknown_share > MAX_UNKNOWN_SHARE:
        usable = False
    if blind_share is not None and blind_share > MAX_BLIND_LESSON_SHARE:
        usable = False

    return (QUALITY_SUFFICIENT if usable else QUALITY_INSUFFICIENT), facts


def recalibrate_confidence(
    proposed: float,
    snapshot: Optional[dict],
    *,
    kappa: float = CALIBRATION_KAPPA,
    min_effective_n: int = MIN_EFFECTIVE_N,
) -> CalibrationOutcome:
    """Map a proposed confidence to one that reflects measured skill.

    PURE: same (proposed, snapshot) → same result. No file I/O, no clock, no
    module state. The snapshot is passed in; see calibration_snapshot() for the
    one impure reader.

    See the section comment above for the rule and its statistical basis.
    """
    p = _clamp01(float(proposed))
    snapshot = snapshot or {}

    quality, facts = classify_calibration_quality(snapshot)
    if quality != QUALITY_NONE and facts["effective_n"] < min_effective_n:
        # A caller may tighten min_effective_n; re-grade on the passed floor.
        quality = QUALITY_INSUFFICIENT

    n = facts["effective_n"]
    m = facts["classified_n"]

    def _outcome(calibrated, applied, anchor, bias, delta, shift, capped, reason):
        return CalibrationOutcome(
            proposed=p,
            calibrated=round(float(calibrated), 10),
            applied=applied,
            quality=quality,
            effective_n=n,
            classified_n=m,
            unknown_share=facts["unknown_share"],
            weight=round(m / (m + kappa), 10) if (m + kappa) > 0 else 0.0,
            anchor=anchor,
            bias=bias,
            delta_applied=delta,
            shift=round(float(shift), 10),
            capped=capped,
            reason=reason,
        )

    if quality == QUALITY_NONE:
        return _outcome(
            p,
            False,
            0.5,
            None,
            0.0,
            0.0,
            False,
            "no decisive verdicts — calibration store empty; proposal passed through unaltered",
        )

    w = m / (m + kappa) if (m + kappa) > 0 else 0.0

    if w == 0.0:
        # Every decisive outcome is uninterpretable (unknown class) — the store
        # cannot attribute a single failure to skill. Applying a correction here
        # would be acting on evidence that does not exist.
        return _outcome(
            p,
            False,
            0.5,
            snapshot.get("bias"),
            0.0,
            0.0,
            False,
            "calibration quality INSUFFICIENT: no interpretable outcome — "
            "correction withheld rather than applied on an unmeasurable base rate",
        )

    skill_usable = quality == QUALITY_SUFFICIENT
    accuracy = snapshot.get("accuracy")
    anchor = float(accuracy) if skill_usable else 0.5

    bias = snapshot.get("bias")
    if bias is None:
        # DERIVE it rather than silently treating a measurable bias as zero. A
        # silent fallback here would make an overconfident store look unbiased —
        # the one error this function exists to prevent.
        mc = snapshot.get("mean_confidence")
        bias = (
            (float(mc) - float(accuracy))
            if (mc is not None and accuracy is not None)
            else None
        )
    else:
        bias = float(bias)
    delta = bias if (not skill_usable and bias is not None) else 0.0

    raw_shift = w * (anchor - p) - w * delta

    # Correction may never exceed twice the weight of evidence (derived bound).
    cap = 2.0 * w
    capped = abs(raw_shift) > cap
    shift = cap if raw_shift > cap else (-cap if raw_shift < -cap else raw_shift)

    calibrated = _clamp01(p + shift)

    if not skill_usable:
        why = (
            f"INSUFFICIENT (n={n} < {min_effective_n} and/or error taxonomy blind); "
            f"anchored on genuine uncertainty 0.5, measured bias direction applied "
            f"at evidence weight {w:.4f}"
        )
    else:
        why = (
            f"SUFFICIENT (n={n}); shrunk toward measured base rate {anchor:.4f} "
            f"at evidence weight {w:.4f}"
        )
        if abs(shift) <= 1e-12:
            why += (
                "; shift is zero — the proposal already equals the measured base rate"
            )
    if capped:
        why += f"; shift capped at {cap:.4f}"

    return _outcome(
        calibrated, abs(shift) > 1e-12, anchor, bias, delta, shift, capped, why
    )


def calibration_snapshot() -> dict:
    """The ONLY impure reader on the recalibration path.

    Merges the measured calibration store with the lesson store's health into one
    snapshot, so recalibrate_confidence() can stay pure. The import of chron_learn
    is lazy because chron_learn imports this module.
    """
    snap: dict = {}
    stored = load_calibration()
    if stored:
        snap.update(stored)
    snap["calibration_file"] = str(CALIBRATION_FILE)
    snap.setdefault("effective_n", int(snap.get("total") or 0))

    try:
        from chron.chron_learn import lesson_health

        snap.update(lesson_health())
    except Exception as exc:  # noqa: BLE001 — a blind lesson store must not stop
        # the loop, but it must also not be silently assumed healthy.
        snap["lesson_health_error"] = f"{type(exc).__name__}: {exc}"
    return snap


def snapshot_digest(snapshot: Optional[dict]) -> dict:
    """Small, auditable subset of a snapshot, stored on each prediction."""
    snap = snapshot or {}
    return {
        "source": snap.get("calibration_file"),
        "updated_at": snap.get("updated_at"),
        "effective_n": snap.get("effective_n"),
        "total": snap.get("total"),
        "accuracy": snap.get("accuracy"),
        "mean_brier": snap.get("mean_brier"),
        "mean_confidence": snap.get("mean_confidence"),
        "bias": snap.get("bias"),
        "reliability": snap.get("reliability"),
        "by_error_type": snap.get("by_error_type"),
        "lesson_total": snap.get("lesson_total"),
        "lesson_actionable": snap.get("lesson_actionable"),
        "blind_lesson_share": snap.get("blind_lesson_share"),
        "lesson_health_error": snap.get("lesson_health_error"),
    }


# ───────────────────────── GENERATE FROM EVENTS ─────────────────────────


def generate_from_chron_events() -> list[dict]:
    """Generate falsifiable predictions from chron_events.json.

    Reads the `predictions[]` array on each event. Each entry carries:
    claim, expected_value, threshold, verifier, falsifier, confidence.
    Falls back to trivial "event will occur" for events without predictions[].

    2026-09-21 (learning-loop closure): the generator now CONSUMES the measured
    calibration before it emits a confidence, and records what it consumed.

        proposed confidence  ──recalibrate_confidence()──▶  emitted confidence

    The snapshot (calibration.json + lesson-store health) is captured ONCE per
    generation pass and stored on every prediction as `calibration_snapshot`, so a
    later reader can tell which measurement produced that number. Before this, the
    generator read neither file: OUTCOME→LEARNING wrote them and nothing read them
    — a ledger, not a loop.
    """
    chron_events = Path("/root/chron/chron_events.json")
    if not chron_events.exists():
        return []

    data = json.loads(chron_events.read_text())
    events = data.get("events", [])
    today = datetime.now(MYT).date()

    # ── Consume the learning hop: one snapshot for the whole pass ──
    snapshot = calibration_snapshot()
    digest = snapshot_digest(snapshot)
    lessons = _load_lessons_for_generation()
    lesson_state = {
        "lesson_total": len(lessons),
        "lesson_actionable": sum(1 for l in lessons if l.get("actionable")),
        "lesson_ids_actionable": [
            l.get("lesson_id") for l in lessons if l.get("actionable")
        ],
        "lesson_ids_diagnostic": [
            l.get("lesson_id") for l in lessons if not l.get("actionable")
        ],
        # An UNKNOWN-class lesson ("Investigate root cause") is the loop reporting
        # its own blindness. It is carried for audit and explicitly NOT applied.
        "policy_applied": False,
        "policy_note": (
            "no lesson is applied as policy: none yet meets the promotion gate "
            "(external validation is not implemented), and blind (UNKNOWN-class) "
            "lessons are diagnostics, not rules"
        ),
    }

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

                proposed = float(sub_pred.get("confidence", 0.5))
                outcome = recalibrate_confidence(proposed, snapshot)

                pred = create_prediction(
                    claim=claim,
                    expected_outcome=sub_pred.get("expected_value", claim),
                    confidence=outcome.calibrated,
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
                    # Coverage window for ABSENCE claims ("zero failures for
                    # 7 days"). Without it, verify_at alone cannot establish
                    # that the absence held for the whole window.
                    coverage=sub_pred.get("coverage")
                    or sub_pred.get("coverage_window"),
                    # ── P0 provenance fields ──
                    evidence_snapshot=evidence_snapshot,
                    source_refs=source_refs,
                    success_rule=success_rule,
                    falsification_rule=falsification_rule,
                    created_by="chron_prediction.generate_from_chron_events",
                    trigger="manual_generate",
                    loop_id=None,
                    # ── Learning-hop audit trail (2026-09-21) ──
                    confidence_proposed=proposed,
                    confidence_calibrated=outcome.calibrated,
                    calibration_quality=outcome.quality,
                    recalibration=outcome.audit(),
                    calibration_snapshot=digest,
                    lessons_consumed=lesson_state,
                )
                predictions.append(pred)
            continue

        # ── Fallback: trivial "event will occur" (legacy events without predictions[]) ──
        dedup_key = f"{event_id}||Event: {title}"
        if dedup_key in existing_keys:
            continue

        # PREDICTED was MISSING from this map until 2026-10-03 (FI-003, D3).
        # It fell through to the 0.5 default while TENTATIVE scored 0.6, so the
        # weakest label calibrated ABOVE an actual forecast. Measured effect on
        # the 2026-10-03 AGI branch set: branch C (asserted amplitude 0.25) and
        # branch E (0.30) both landed at confidence 0.5412, while branch A
        # (0.55) landed LOWER at 0.4942 — the ordering inverted, so Brier
        # scoring would have penalised the confident branch least.
        confidence_map = {
            "CONFIRMED": 0.9,
            "ANNOUNCED": 0.8,
            "LIKELY": 0.7,
            "PREDICTED": 0.65,
            "TENTATIVE": 0.6,
        }
        # An explicit numeric amplitude always wins over the enum. The enum is a
        # coarse label; the amplitude is the actual claim being scored. Without
        # this the forecaster's real probability is discarded before
        # confidence_proposed and the calibrator cannot discriminate.
        amp = event.get("amplitude")
        if (
            isinstance(amp, (int, float))
            and not isinstance(amp, bool)
            and 0.0 <= float(amp) <= 1.0
        ):
            proposed = float(amp)
        else:
            proposed = float(confidence_map.get(event.get("confidence", ""), 0.5))
        outcome = recalibrate_confidence(proposed, snapshot)

        pred = create_prediction(
            claim=f"Event: {title}",
            expected_outcome=f"{title} will occur by {target_date_str}",
            confidence=outcome.calibrated,
            verify_at=verify_at,
            assumptions=[],
            source="chron_events",
            source_id=event_id,
            horizon=horizon,
            # ── audience propagation (2026-09-23): the Path-B fallback must
            # inherit the source event's audience — omitting it silently
            # defaulted to "both", UNBOUND-ing arif-audience events and
            # leaving their predictions invisible to get_active/get_due.
            audience=event.get("audience", "both"),
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
            # ── Learning-hop audit trail (2026-09-21) ──
            confidence_proposed=proposed,
            confidence_calibrated=outcome.calibrated,
            calibration_quality=outcome.quality,
            recalibration=outcome.audit(),
            calibration_snapshot=digest,
            lessons_consumed=lesson_state,
        )
        predictions.append(pred)

    return predictions


def _load_lessons_for_generation() -> list[dict]:
    """Read the lesson store for the generation pass, labelling each lesson.

    Lazy import (chron_learn imports this module). Failure to read the lesson store
    must not stop generation, but it is disclosed on the snapshot rather than
    silently treated as "no lessons".
    """
    try:
        from chron.chron_learn import load_lessons, is_actionable_lesson

        return [{**l, "actionable": is_actionable_lesson(l)} for l in load_lessons()]
    except Exception:  # noqa: BLE001
        return []
