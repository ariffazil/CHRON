"""CHRON Verify — checks predictions at verify_at.

The verification engine runs on a schedule (or manually).
For each due prediction:
  1. Check if the event/outcome can be observed
  2. Classify: CORRECT, INCORRECT, UNVERIFIABLE
  3. Compute Brier score
  4. Emit verify episode
  5. Feed result to chron_learn for lesson extraction

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from chron.chron_prediction import (
    get_due,
    get_active,
    verify_prediction,
    compute_calibration,
    save_calibration,
    load_predictions,
)
from chron.chron_episode import verify_from_result
from chron.chron_store import get_store

MYT = timezone(timedelta(hours=8))
CHRON_EVENTS = Path("/root/AAA/scripts/chron_events.json")
VERIFICATION_LOG = Path("/root/chron/data/verification_log.jsonl")
# Attempt log is deliberately SEPARATE from the canonical verification log.
# 2026-09-18 repair (D4): two writers with two different schemas were appending
# to verification_log.jsonl, producing a mixed-schema file where legacy rows
# could not be distinguished from canonical verdicts. Non-verdict attempts now
# go to their own file; verification_log.jsonl holds only canonical records.
ATTEMPTS_LOG = Path("/root/chron/data/verification_attempts.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── EVIDENCE ACQUISITION ─────────────────────────
#
# 2026-09-18 (loop-closure repair, V-series): the verifier previously had TWO
# implementations — this module and chron_cron_verify.py — and the cron copy
# decided outcomes by asserting that a passed calendar date meant the claim
# held. That minted CORRECT verdicts with no evidence. Evidence acquisition now
# lives HERE, once, and it either returns an observed outcome backed by a
# quotable source or it returns None. Absence of evidence is never converted
# into a verdict.

SEARXNG_URL = "http://127.0.0.1:8080/search"


def _search(query: str, timeout: int = 10) -> list[dict]:
    """SearXNG query. Returns [] on any failure — never fabricates a result."""
    import urllib.parse
    import urllib.request

    url = f"{SEARXNG_URL}?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data.get("results", []) or []
    except Exception:
        return []


# Words that indicate the claim was satisfied / not satisfied. Deliberately
# narrow: a loose match here becomes a false verdict in the calibration ledger.
_CONFIRM = ("dibentang", "tabled", "presented", "announced", "diumumkan",
            "berkuat kuasa", "implemented", "gazetted")
_DENY = ("delayed", "postponed", "ditangguh", "cancelled", "dibatalkan",
         "deferred", "withdrawn")

_STOPWORDS = {
    "the", "and", "for", "with", "from", "will", "malaysia", "malaysian",
    "government", "federal", "year", "that", "this", "into", "over", "than",
}


def _title_tokens(title: str) -> set[str]:
    """Distinctive words from an event title, used to bind a snippet to a claim."""
    raw = "".join(c.lower() if c.isalnum() else " " for c in title).split()
    return {w for w in raw if len(w) > 3 and w not in _STOPWORDS}


def _evidence_for(pred: dict, event: dict) -> Optional[dict]:
    """Attempt to observe the outcome of a prediction. None when unobserved.

    Returns {"observed", "error", "error_class", "source"} only when a source
    actually speaks to the claim. Declining is the normal outcome; a verdict is
    the exception.

    Threshold/magnitude claims DELIBERATELY always decline. An earlier version
    of this function took the largest number appearing anywhere in the search
    snippets and compared it to the claim's threshold. Probing it against a real
    magnitude claim returned "Reported value ~2026.0 MYR" — 2026 was a year in
    the page text — and produced error=1.0, a decisive INCORRECT verdict
    manufactured from unrelated digits. Comparing a magnitude requires a
    unit-aware extractor over a named data source, not a regex over prose. Until
    one exists, these claims stay open. A false verdict is worse than no verdict:
    it corrupts the calibration ledger that the whole organ exists to keep honest.
    """
    if pred.get("threshold") is not None:
        return None

    title = event.get("title", "")
    target = event.get("target_date", "")
    tokens = _title_tokens(title)

    results = _search(f"{title} {target}")
    if not results:
        return None

    # A snippet only counts if it actually talks about this event: require two
    # distinctive title tokens present together with a confirm/deny word. This
    # is what stops a generic "Malaysia — Wikipedia" hit from casting a vote.
    for r in results[:5]:
        text = str(r.get("content") or r.get("title") or "")
        lower = text.lower()
        if not lower.strip():
            continue
        if len(tokens & _title_tokens(text)) < 2:
            continue

        source = str(r.get("url") or r.get("title") or "searxng")[:160]
        if any(w in lower for w in _DENY):
            return {
                "observed": f"Source reports the event was delayed or cancelled: {text[:140]}",
                "error": 1.0,
                "error_class": "REGIME_CHANGE",
                "source": source,
            }
        if any(w in lower for w in _CONFIRM):
            return {
                "observed": f"Source reports the event occurred: {text[:140]}",
                "error": 0.0,
                "error_class": "NONE",
                "source": source,
            }

    return None


# ───────────────────────── VERIFICATION ─────────────────────────


def verify_event_prediction(
    pred: dict, use_search: bool = True
) -> tuple[dict, str, Optional[float], str]:
    """Verify a prediction derived from chron_events.

    Returns: (updated_prediction, observed_outcome, error, error_class)

    error is None whenever no decisive evidence was observed. Callers must not
    read None as "incorrect" — None means the claim is still OPEN.
    """
    source_id = pred.get("source_id")
    if not source_id:
        return pred, "UNVERIFIABLE — no source_id", None, "DATA_ERROR"

    if not CHRON_EVENTS.exists():
        return pred, "UNVERIFIABLE — chron_events.json not found", None, "DATA_ERROR"

    try:
        events = json.loads(CHRON_EVENTS.read_text()).get("events", [])
    except Exception:
        return pred, "UNVERIFIABLE — cannot parse chron_events", None, "DATA_ERROR"

    event = next((e for e in events if e.get("id") == source_id), None)
    if not event:
        return pred, "UNVERIFIABLE — source event not found", None, "DATA_ERROR"

    target_date_str = event.get("target_date")
    today = datetime.now(MYT).date()

    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return pred, "UNVERIFIABLE — no target_date", None, "DATA_ERROR"

    if today < target_date:
        return pred, f"NOT YET DUE — target is {target_date_str}", None, "PENDING"

    # Target date passed. A closed window is NOT a verified claim.
    #
    # 2026-09-18 repair (D5/V3, assumption-laundering defect): the previous
    # branches returned error=0.0 ("CORRECT") for FISCAL/REGULATORY/MARKET purely
    # because a calendar date had passed, or because a search backend was
    # unreachable. For a claim whose magnitude matters, neither fact says anything
    # about the magnitude. That minted fake correctness into the calibration
    # ledger. Now: gather real evidence, or return UNVERIFIABLE and stay open.
    #
    # Evidence path: /root/chron/REPAIR-RECEIPT-2026-09-18.md
    # Rollback: /root/chron/.backup-chron-repair-20260918T152417Z/chron_verify.py
    kind = event.get("kind", "UNKNOWN")

    if use_search:
        ev = _evidence_for(pred, event)
        if ev is not None:
            return pred, ev["observed"], ev["error"], ev["error_class"]

    observed = (
        f"Window closed {target_date_str} (kind={kind}). "
        "No outcome evidence observed from available sources — claim stays open."
    )
    return pred, observed, None, "AWAITING_EVIDENCE"


# ───────────────────────── RUN VERIFICATION ─────────────────────────


def run_verification(dry_run: bool = False) -> dict:
    """Run verification on all due predictions.

    Returns summary dict.
    """
    due = get_due()
    now = datetime.now(MYT).strftime("%Y-%m-%d %H:%M MYT")

    if not due:
        return {
            "timestamp": now,
            "due": 0,
            "verified": 0,
            "unverifiable": 0,
            "message": "No predictions due for verification.",
        }

    results = []
    store = get_store()

    for pred in due:
        pred_id = pred["prediction_id"]

        # Idempotency FIRST — never re-derive a record we already hold.
        if _is_already_verified(pred_id):
            results.append(
                {
                    "prediction_id": pred_id,
                    "claim": pred.get("claim", "?"),
                    "observed": None,
                    "error": None,
                    "error_class": "ALREADY_VERIFIED",
                    "status": "ALREADY_VERIFIED",
                    "mutation": "none",
                }
            )
            continue

        # Verify based on source
        if pred.get("source") == "chron_events":
            pred, observed, error, error_class = verify_event_prediction(pred)
        else:
            observed = "UNKNOWN — no verification method for source"
            error, error_class = None, "AWAITING_EVIDENCE"

        if dry_run:
            results.append(
                {
                    "prediction_id": pred_id,
                    "claim": pred.get("claim", "?"),
                    "observed": observed,
                    "error": error,
                    "error_class": error_class,
                    "status": "WOULD_VERIFY" if error is not None else "WOULD_STAY_OPEN",
                    "mutation": "dry_run",
                }
            )
            continue

        if error is None:
            # Verification was ATTEMPTED and found no evidence. Log the attempt
            # to the attempts ledger only — the canonical verification log stays
            # verdict-only, and the prediction stays open for a later retry.
            _log_verification(
                pred_id,
                pred.get("claim", "?"),
                pred.get("expected_outcome", "?"),
                observed,
                error,
                error_class,
                "UNVERIFIABLE",
            )
            results.append(
                {
                    "prediction_id": pred_id,
                    "claim": pred.get("claim", "?"),
                    "observed": observed,
                    "error": None,
                    "error_class": error_class,
                    "status": "UNVERIFIABLE",
                    "mutation": "attempt_logged",
                }
            )
            continue

        # Decisive verdict — record it.
        correct = error == 0.0
        verif_record = verify_prediction(pred, observed, correct)
        _append_verification_record(verif_record)

        # Create verify episode (records the act, not the mutation)
        ep = verify_from_result(pred, observed, error, error_class)
        store.append(ep)

        _log_verification(
            pred_id,
            pred.get("claim", "?"),
            pred.get("expected_outcome", "?"),
            observed,
            error,
            error_class,
            verif_record["verdict"],
        )

        results.append(
            {
                "prediction_id": pred_id,
                "claim": pred.get("claim", "?"),
                "observed": observed,
                "error": error,
                "error_class": error_class,
                "status": "VERIFIED",
                "mutation": "recorded",
            }
        )

    # NOTE: We do NOT rewrite predictions.jsonl — predictions are immutable at birth.
    # Verification results live in verification_log.jsonl (APPEND-ONLY).
    # Calibration is computed at read time by joining predictions with verification_log.
    if not dry_run:
        calibration = compute_calibration()
        save_calibration(calibration)

    verified_count = sum(
        1 for r in results if r["status"] == "VERIFIED" and r["error"] == 0.0
    )
    incorrect_count = sum(
        1 for r in results if r["status"] == "VERIFIED" and (r["error"] or 0) > 0.0
    )
    unverifiable_count = sum(1 for r in results if r["status"] == "UNVERIFIABLE")

    return {
        "timestamp": now,
        "due": len(due),
        "verified_correct": verified_count,
        "verified_incorrect": incorrect_count,
        "unverifiable": unverifiable_count,
        "results": results,
    }


def _log_verification(pred_id, claim, expected, observed, error, error_class, status):
    """Append a verification ATTEMPT to the attempts ledger (non-canonical).

    2026-09-18 repair (D4): this used to append to verification_log.jsonl with a
    different schema than _append_verification_record(), which is what produced a
    mixed-schema canonical log. Attempts — including UNVERIFIABLE ones — are
    recorded here; verification_log.jsonl now holds only canonical verdict
    records written by _append_verification_record().
    """
    ATTEMPTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _now_iso(),
        "prediction_id": pred_id,
        "claim": claim,
        "expected": expected,
        "observed": observed,
        "error": error,
        "error_class": error_class,
        "status": status,
    }
    with open(ATTEMPTS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    # Emit to arifFlow (makes verification visible to federation)
    _emit_ariflow_verify(pred_id, claim, observed, error, error_class, status)


def _append_verification_record(verif_record: dict) -> None:
    """Append a full verification record (preserves birth snapshot) to verification_log.jsonl.

    This is the canonical write path for verification results.
    Predictions are NEVER overwritten — this record captures both
    the original belief state (claim, confidence, evidence_snapshot)
    AND the observed outcome + verdict.
    """
    VERIFICATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(VERIFICATION_LOG, "a") as f:
        f.write(json.dumps(verif_record, default=str) + "\n")


def _is_already_verified(pred_id: str) -> bool:
    """Idempotency check: does this prediction already carry a DECISIVE verdict?

    2026-09-18 repair: presence of a record was not the right test. A boolean
    'is there a row' made a retracted (VOID) or UNVERIFIABLE row permanently
    block re-verification. The correct test is whether the LATEST record for
    this prediction is decisive (CORRECT / INCORRECT). Appending a VOID record
    therefore reopens the prediction.
    """
    if not VERIFICATION_LOG.exists():
        return False
    latest = None
    with open(VERIFICATION_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("prediction_id") == pred_id:
                latest = rec
    if latest is None:
        return False
    verdict = str(latest.get("verdict") or latest.get("status") or "").strip().upper()
    return verdict in ("CORRECT", "INCORRECT", "VERIFIED_CORRECT", "VERIFIED_INCORRECT")


def _emit_ariflow_verify(pred_id, claim, observed, error, error_class, status):
    """Emit verification event as arifFlow receipt."""
    try:
        import urllib.request
        import uuid

        receipt = {
            "receipt_id": str(uuid.uuid4()),
            "created_at": _now_iso(),
            "actor_id": "chron",
            "session_id": f"chron-verify-{pred_id[:12]}",
            "step_type": "Verify",
            "step_number": 0,
            "cost_ns": 0,
            "epistemic_label": "Derivation",
            "floor_verdict": "Pass",
            "cooling_decision": "None",
            "summary": (
                f"CHRON verify: {claim[:60]} → "
                f"error={error} class={error_class} status={status}"
            ),
            "routed_organ": "chron",
            "payload": {
                "prediction_id": pred_id,
                "observed": observed,
                "error": error,
                "error_class": error_class,
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


def _save_all_predictions(predictions: list[dict]) -> None:
    """REMOVED (2026-09-18). Do not reinstate.

    This rewrote `predictions.jsonl` wholesale — the same immutability violation
    that `chron_cron_verify.py` was performing in production (defect V1). It was
    defined here and never called, so it was a loaded gun rather than a live bug:
    one future caller away from destroying the birth snapshots that calibration
    is audited against. Predictions are immutable at birth; verification results
    belong in verification_log.jsonl.

    Kept as a named stub so a future reader who greps for the old symbol finds
    the reason instead of an unexplained gap.
    """
    raise RuntimeError(
        "Refusing to rewrite predictions.jsonl: predictions are immutable at birth. "
        "Record verification results via chron_verify._append_verification_record(). "
        "See /root/chron/REPAIR-RECEIPT-2026-09-18.md"
    )


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    import sys

    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    result = run_verification(dry_run=dry_run)

    print(f"CHRON Verify — {result['timestamp']}")
    print(f"  Due: {result['due']}")
    if result["due"] > 0:
        print(f"  Correct: {result.get('verified_correct', 0)}")
        print(f"  Incorrect: {result.get('verified_incorrect', 0)}")
        print(f"  Unverifiable: {result.get('unverifiable', 0)}")
        for r in result.get("results", []):
            print(f"    [{r['prediction_id'][:12]}] {r['claim'][:50]}")
            print(f"      observed: {str(r['observed'])[:50]}")
            print(
                f"      error: {r['error']}  class: {r['error_class']}  status: {r['status']}"
            )
    else:
        print(f"  {result.get('message', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
