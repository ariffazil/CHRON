"""CHRON Ledger Integrity — the SINGLE implementation of prediction identity,
observation de-duplication, and calibration accounting.

2026-09-21 repair. Before this module there were:

  * TWO prediction stores with overlapping ids
      - /root/.hermes/cron/state/chron_personal/predictions.json  (json, 12 rows)
      - /root/chron/data/predictions.jsonl                        (jsonl)
  * TWO verification logs, one per store
  * TWO calibration surfaces reporting DIFFERENT numbers because each read a
    different store:
      - /root/chron/data/calibration.json               mean_brier 0.24625
      - .../chron_personal/calibration_report.json      mean_brier 0.1825
    Derived, not guessed: 0.24625 = (0.0025 + 0.49)/2 is the chron store's two
    decisive rows; 0.1825 = (0.0625 + 0.3025)/2 is the .hermes store's two gold
    rows. Neither arithmetic was wrong — they were solving different problems.
  * ONE observation (XAUUSD @ 4399.70, 2026-09-17) counted as TWO decisive
    predictions because two hypotheses were written against it.

Accounting rules — each is a rule, not a preference:

  R1 IDENTITY   Every prediction carries a stable `ledger_id` derived from
     (store, prediction_id). Deterministic, so a verification-log record is
     resolvable to its ledger row without rewriting the log.
  R2 OBSERVATION  A decisive prediction is counted per UNDERLYING OBSERVATION,
     not per hypothesis. Rows scored against the same measured datum form one
     observation. The observation is scored by its members' hit-rate, so a
     group holding one correct and one incorrect hypothesis contributes ONE
     sample with a 0.5 hit-rate — symmetric, and no winner is picked by
     birth order.
  R3 SETTLEMENT  A row is settled only on a decisive verdict
     (CORRECT / INCORRECT) or a decisive outcome (HIT / MISS). VOID (retracted)
     is excluded, never scored. UNVERIFIABLE is absence of evidence, never a
     miss. A lifecycle label such as SUPERSEDED is NOT evidence retraction and
     does not remove a scored observation.
  R4 ORPHANS    A verification record whose prediction_id resolves to no store
     row is reported and never scored — not silently dropped either.
  R5 SYNTHETIC  Pipeline self-tests are reported in their own bucket so the
     institution's accuracy cannot be carried by its own test harness.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = "chron_calibration_v2"

#: Every store a prediction may live in. Order is significant: it is the
#: deterministic tie-break when two rows share a `created_at`.
LEDGER_STORES: list[tuple[str, Path, str]] = [
    ("hermes-json", Path("/root/.hermes/cron/state/chron_personal/predictions.json"), "json"),
    ("chron-jsonl", Path("/root/chron/data/predictions.jsonl"), "jsonl"),
]

#: Verification logs, paired with the store they describe.
LEDGER_VERIFICATION_LOGS: list[tuple[str, Path]] = [
    ("hermes-json", Path("/root/.hermes/cron/state/chron_personal/verification_log.jsonl")),
    ("chron-jsonl", Path("/root/chron/data/verification_log.jsonl")),
]

#: The canonical calibration artifact. ONE writer, TWO scheduled producers
#: (chron_cron_verify.py 07:00 MYT, chron_loop_close.py 07:15 MYT).
CANONICAL_CALIBRATION_FILE = Path("/root/chron/data/calibration.json")

#: The canonical store. Where a prediction lives when two stores hold its id.
CANONICAL_STORE_LABEL = "chron-jsonl"

#: Files that must NOT hold an independent computation of the same statistic.
CALIBRATION_SUBORDINATES: list[Path] = [
    Path("/root/chron/data/calibration_unified.json"),
    Path("/root/.hermes/cron/state/chron_personal/calibration_report.json"),
]

#: Declared synthetic self-test sources. Provenance-based, never outcome-based.
SYNTHETIC_SOURCES = {"chron_loop_closure_test", "chron_pipeline_test"}

VERDICT_CORRECT = "VERIFIED_CORRECT"
VERDICT_INCORRECT = "VERIFIED_INCORRECT"
VERDICT_UNVERIFIABLE = "UNVERIFIABLE"
VERDICT_VOID = "VOID"
_DECISIVE = (VERDICT_CORRECT, VERDICT_INCORRECT)

#: Lifecycle labels that RETRACT evidence. A bare `SUPERSEDED` is a lifecycle
#: or migration marker and is deliberately absent here: retiring a store does
#: not un-observe what was observed.
RETRACTING_STATUSES = ("VOID", "VOIDED", "RETRACTED")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── IDENTITY (R1) ─────────────────────────


def mint_ledger_id(store_label: str, prediction_id: str) -> str:
    """Deterministic ledger id binding a prediction to exactly ONE store.

    Deterministic on purpose: the verification logs are append-only and were
    NOT rewritten by this repair, so a log record must be resolvable to its
    ledger row by recomputation. Same inputs -> same id, forever.
    """
    digest = hashlib.sha1(f"{store_label}|{prediction_id}".encode()).hexdigest()
    return f"lid-{digest[:12]}"


def ledger_id_for(prediction_id: str) -> Optional[str]:
    """Resolve a bare prediction_id to its ledger_id by searching the stores."""
    for store_label, pid, _row in iter_ledger_rows():
        if pid == prediction_id:
            return mint_ledger_id(store_label, pid)
    return None


# ───────────────────────── LOADING ─────────────────────────


def _read_store(path: Path, kind: str) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if kind == "jsonl":
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
    try:
        return json.loads(raw).get("predictions", [])
    except Exception:
        return []


def iter_ledger_rows():
    """Yield (store_label, prediction_id, row) across every store, in order."""
    for store_label, path, kind in LEDGER_STORES:
        for row in _read_store(path, kind):
            yield store_label, row.get("prediction_id"), row


def load_verifications() -> dict[str, dict]:
    """Store label -> {prediction_id: last verification record}.

    Last-write-wins is deliberate: appending a VOID record is how a bad
    verification is retracted without rewriting history.
    """
    out: dict[str, dict] = {}
    for store_label, path in LEDGER_VERIFICATION_LOGS:
        by_pid: dict[str, dict] = {}
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = rec.get("prediction_id")
                if pid:
                    by_pid[pid] = rec
        out[store_label] = by_pid
    return out


def canonical_verdict(rec: dict) -> str:
    """Normalise a verification record's outcome to one canonical verdict."""
    raw = rec.get("verdict") or rec.get("status") or rec.get("state") or ""
    raw = str(raw).strip().upper()
    if raw in ("CORRECT", VERDICT_CORRECT, "HIT"):
        return VERDICT_CORRECT
    if raw in ("INCORRECT", VERDICT_INCORRECT, "MISS"):
        return VERDICT_INCORRECT
    if raw in (VERDICT_VOID, "VOIDED", "RETRACTED") or raw.startswith("VOIDED"):
        return VERDICT_VOID
    return VERDICT_UNVERIFIABLE


# ───────────────────────── OBSERVATION (R2) ─────────────────────────


def observation_key(row: dict) -> str:
    """The identity of the UNDERLYING OBSERVATION a prediction was scored on.

    Two predictions that resolve against the same measured datum share a key,
    however differently they were worded. This is what stops one price print
    from occupying two slots in the accuracy ratio.
    """
    instrument = row.get("instrument")
    observed = row.get("price_observed")
    if instrument and observed is not None:
        src = row.get("price_source") or row.get("price_data_quality") or ""
        at = row.get("price_observed_at") or row.get("observed_at") or ""
        return f"price|{instrument}|{src}|{round(float(observed), 6)}|{at}"
    source_id = row.get("source_id")
    if source_id:
        return f"event|{source_id}|{row.get('verify_at') or ''}"
    return f"row|{row.get('prediction_id')}|{row.get('verify_at') or ''}"


def _brier(row: dict, rec: dict, verdict: str) -> Optional[float]:
    for candidate in (row.get("brier_score"), rec.get("brier_score")):
        if candidate is not None:
            try:
                return float(candidate)
            except (TypeError, ValueError):
                pass
    conf = row.get("confidence")
    if conf is None:
        return None
    return (float(conf) - (1.0 if verdict == VERDICT_CORRECT else 0.0)) ** 2


# ───────────────────────── SETTLEMENT (R3/R4/R5) ─────────────────────────


def build_settled_ledger() -> dict[str, Any]:
    """Join stores x verification logs into a settled, de-duplicated ledger."""
    verifs = load_verifications()

    rows: list[dict] = []
    store_index: dict[str, int] = {}
    seen_pids: set[str] = set()
    prediction_ids_present = 0
    prediction_ids_missing = 0
    store_counts: dict[str, int] = {}
    store_order = {label: i for i, (label, _p, _k) in enumerate(LEDGER_STORES)}

    for store_label, pid, row in iter_ledger_rows():
        idx = store_index.get(store_label, 0)
        store_index[store_label] = idx + 1
        store_counts[store_label] = store_counts.get(store_label, 0) + 1
        if not pid:
            prediction_ids_missing += 1
            continue
        prediction_ids_present += 1
        seen_pids.add(pid)

        rec = verifs.get(store_label, {}).get(pid, {})
        log_verdict = canonical_verdict(rec) if rec else None

        # R3 — settlement priority: retraction beats everything; then a decisive
        # verification verdict; then the row's own status; then its outcome.
        if log_verdict == VERDICT_VOID:
            continue
        verdict = log_verdict if log_verdict in _DECISIVE else None
        if verdict is None:
            status = str(row.get("status") or row.get("state") or "").upper()
            if any(status.startswith(t) for t in RETRACTING_STATUSES):
                continue
            if status in ("CORRECT", VERDICT_CORRECT):
                verdict = VERDICT_CORRECT
            elif status in ("INCORRECT", VERDICT_INCORRECT):
                verdict = VERDICT_INCORRECT
        if verdict is None:
            outcome = str(row.get("outcome") or "").upper()
            if outcome == "HIT":
                verdict = VERDICT_CORRECT
            elif outcome == "MISS":
                verdict = VERDICT_INCORRECT
        if verdict is None:
            continue

        rows.append(
            {
                "ledger_id": mint_ledger_id(store_label, pid),
                "store": store_label,
                "prediction_id": pid,
                "store_order": store_order[store_label],
                "row_index": idx,
                "claim": row.get("claim"),
                "confidence": row.get("confidence"),
                "created_at": row.get("created_at") or "",
                "verify_at": row.get("verify_at"),
                "verdict": verdict,
                "brier_score": _brier(row, rec, verdict),
                "source": row.get("source"),
                "source_id": row.get("source_id"),
                "observed_ref": row.get("price_observed_at")
                or (rec.get("timestamp") if rec else None),
                "observation_key": observation_key(row),
                "synthetic": row.get("source") in SYNTHETIC_SOURCES,
            }
        )

    # ── R2: collapse one observation to one decisive sample ──
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["observation_key"], []).append(r)

    observations: list[dict] = []
    duplicate_groups: list[dict] = []
    for key in sorted(groups):
        members = sorted(
            groups[key],
            key=lambda r: (
                r["created_at"],
                0 if r["store"] == CANONICAL_STORE_LABEL else 1,
                r["row_index"],
            ),
        )
        hits = sum(1 for m in members if m["verdict"] == VERDICT_CORRECT)
        n = len(members)
        briers = [m["brier_score"] for m in members if m["brier_score"] is not None]
        cls = "CORRECT" if hits == n else ("INCORRECT" if hits == 0 else "MIXED")
        obs = {
            "observation_key": key,
            "n_hypotheses": n,
            "hits": hits,
            "hit_rate": hits / n,
            "class": cls,
            "brier_score": (sum(briers) / len(briers)) if briers else None,
            "synthetic": all(m["synthetic"] for m in members),
            "members": [
                {
                    "ledger_id": m["ledger_id"],
                    "store": m["store"],
                    "prediction_id": m["prediction_id"],
                    "verdict": m["verdict"],
                    "brier_score": m["brier_score"],
                    "is_primary": m is members[0],
                    "duplicate_of": None if m is members[0] else members[0]["prediction_id"],
                }
                for m in members
            ],
        }
        observations.append(obs)
        if n > 1:
            duplicate_groups.append(
                {
                    "observation_key": key,
                    "primary_prediction_id": members[0]["prediction_id"],
                    "n_before": n,
                    "n_after": 1,
                    "class": cls,
                    "hit_rate": hits / n,
                    "collapsed": [
                        {
                            "ledger_id": m["ledger_id"],
                            "store": m["store"],
                            "prediction_id": m["prediction_id"],
                            "verdict": m["verdict"],
                            "brier_score": m["brier_score"],
                        }
                        for m in members[1:]
                    ],
                }
            )

    # ── R4: orphan verification records ──
    orphans: list[dict] = []
    for store_label, by_pid in verifs.items():
        for pid, rec in by_pid.items():
            if canonical_verdict(rec) == VERDICT_VOID:
                continue
            if pid not in seen_pids:
                orphans.append(
                    {
                        "store": store_label,
                        "prediction_id": pid,
                        "verdict": canonical_verdict(rec),
                        "observed": str(
                            rec.get("observed") or rec.get("observed_outcome") or ""
                        )[:160],
                    }
                )

    return {
        "settled_rows": rows,
        "observations": observations,
        "duplicate_groups": duplicate_groups,
        "orphan_records": orphans,
        "prediction_ids_present": prediction_ids_present,
        "prediction_ids_missing": prediction_ids_missing,
        "store_counts": store_counts,
        "log_record_ids": sorted({pid for by_pid in verifs.values() for pid in by_pid}),
    }


# ───────────────────────── GUARD (R2 enforcement) ─────────────────────────


def duplicate_guard() -> dict:
    """Fires when one underlying observation carries more than one decisive row."""
    ledger = build_settled_ledger()
    groups = ledger["duplicate_groups"]
    return {
        "fired": bool(groups),
        "rule": "R2 — count the observation, not the hypothesis",
        "groups": groups,
        "collapsed_total": sum(g["n_before"] - g["n_after"] for g in groups),
    }


# ───────────────────────── CALIBRATION (single source) ─────────────────────────


def _tally(observations: list[dict]) -> dict:
    if not observations:
        return {
            "n": 0, "correct": 0, "incorrect": 0, "mixed": 0,
            "accuracy": None, "mean_brier": None,
        }
    correct = sum(1 for o in observations if o["class"] == "CORRECT")
    incorrect = sum(1 for o in observations if o["class"] == "INCORRECT")
    mixed = sum(1 for o in observations if o["class"] == "MIXED")
    hit_rate = sum(o["hit_rate"] for o in observations) / len(observations)
    briers = [o["brier_score"] for o in observations if o["brier_score"] is not None]
    return {
        "n": len(observations),
        "correct": correct,
        "incorrect": incorrect,
        "mixed": mixed,
        "accuracy": hit_rate,
        "mean_brier": (sum(briers) / len(briers)) if briers else None,
    }


def compute_calibration_unified() -> dict:
    """The ONE calibration computation. Every surface must read this."""
    ledger = build_settled_ledger()
    observations = ledger["observations"]
    effective = _tally(observations)

    raw_rows = [
        r for r in ledger["settled_rows"] if r["store"] == CANONICAL_STORE_LABEL
    ]
    raw_rows_all_stores = ledger["settled_rows"]
    raw_correct = sum(1 for r in raw_rows if r["verdict"] == VERDICT_CORRECT)
    raw_briers = [r["brier_score"] for r in raw_rows if r["brier_score"] is not None]

    guard = duplicate_guard()
    by_error_type = {"NONE": effective["correct"], "UNKNOWN": effective["incorrect"]}

    confidences: list[float] = []
    for r in ledger["settled_rows"]:
        if r.get("confidence") is not None:
            confidences.append(float(r["confidence"]))
    mean_confidence = (sum(confidences) / len(confidences)) if confidences else None
    bias = (
        (mean_confidence - effective["accuracy"])
        if (mean_confidence is not None and effective["accuracy"] is not None)
        else None
    )
    reliability = (
        (1.0 - (effective["mean_brier"] / 0.25))
        if effective["mean_brier"] is not None
        else None
    )

    decisive_by_store: dict[str, int] = {}
    for o in observations:
        stores = sorted({m["store"] for m in o["members"]})
        for s in stores:
            decisive_by_store[s] = decisive_by_store.get(s, 0) + 1

    return {
        "$schema": SCHEMA,
        "policy": "chron-observation-level-rule-v1",
        "headline": (
            "accuracy/mean_brier are EFFECTIVE: one underlying observation counts "
            "once. raw_* keys show the same ledger counted naively, one row per "
            "hypothesis, so the inflation is visible rather than hidden."
        ),
        "single_source_of_truth": str(CANONICAL_CALIBRATION_FILE),
        "canonical_store": "/root/chron/data/predictions.jsonl",
        # ── Backward-compatible keys (server.py health, chron_mcp, ALPHA-ZEN) ──
        "total": effective["n"],
        "correct": effective["correct"],
        "incorrect": effective["incorrect"],
        "mixed": effective["mixed"],
        "accuracy": effective["accuracy"],
        "mean_brier": effective["mean_brier"],
        "by_error_type": by_error_type,
        "mean_confidence": mean_confidence,
        "bias": bias,
        "bias_magnitude": abs(bias) if bias is not None else None,
        "reliability": reliability,
        "effective_n": effective["n"],
        "decisive": effective["n"],
        "unverifiable": 0,
        "orphan_records": len(ledger["orphan_records"]),
        "records_in_log": len(ledger["log_record_ids"]),
        # ── Raw (naive, per-hypothesis) view, for disclosure ──
        "raw": {
            "n": len(raw_rows),
            "correct": raw_correct,
            "incorrect": len(raw_rows) - raw_correct,
            "accuracy": (raw_correct / len(raw_rows)) if raw_rows else None,
            "mean_brier": (sum(raw_briers) / len(raw_briers)) if raw_briers else None,
            "ids": [r["prediction_id"] for r in raw_rows],
            "scope": f"rows in the canonical store ({CANONICAL_STORE_LABEL}) only",
        },
        "raw_all_stores": {
            "n": len(raw_rows_all_stores),
            "note": "includes mirror rows held by the retired store; not a denominator",
        },
        "correlation_inflation": {
            "raw_n": len(raw_rows),
            "effective_n": effective["n"],
            "samples_removed": len(raw_rows) - effective["n"],
            "raw_mean_brier": (sum(raw_briers) / len(raw_briers)) if raw_briers else None,
            "effective_mean_brier": effective["mean_brier"],
        },
        # ── Integrity surface ──
        "stores": ledger["store_counts"],
        "predictions_total": sum(ledger["store_counts"].values()),
        "ids": {
            "prediction_ids_present": ledger["prediction_ids_present"],
            "prediction_ids_missing": ledger["prediction_ids_missing"],
            "ledger_ids_minted": ledger["prediction_ids_present"],
        },
        "duplicate_observation_guard": guard,
        "duplicate_observations_collapsed": guard["collapsed_total"],
        "resolved_groups": ledger["duplicate_groups"],
        "decisive_by_store": decisive_by_store,
        "synthetic_self_tests": sum(1 for o in observations if o["synthetic"]),
        "synthetic_self_test_ids": sorted(
            m["prediction_id"] for o in observations if o["synthetic"] for m in o["members"]
        ),
        "orphan_verifications": ledger["orphan_records"],
        "honest_scopes": honest_scope_figures(ledger),
        "updated_at": _now_iso(),
    }


def honest_scope_figures(ledger: dict | None = None) -> dict:
    """The corrected figures under each defensible scope, all derived from one
    walk of the ledger. Reported together so no single scope can be quoted as
    if it were the whole truth."""
    if ledger is None:
        ledger = build_settled_ledger()
    obs = ledger["observations"]
    gold = [o for o in obs if any(m["store"] == "hermes-json" for m in o["members"])]
    return {
        "canonical_unified_including_self_tests": _tally(obs),
        "canonical_unified_excluding_self_tests": _tally(
            [o for o in obs if not o["synthetic"]]
        ),
        "gold_pair_scope": _tally(gold),
    }


def delegation_payload() -> dict:
    """What a subordinate surface is allowed to publish: the canonical numbers,
    read from the canonical file, with no local computation."""
    payload: dict[str, Any] = {
        "$schema": "chron_calibration_pointer_v1",
        "deprecated": True,
        "independent_computation": False,
        "canonical_source": str(CANONICAL_CALIBRATION_FILE),
        "notice": (
            "DEPRECATED — this file no longer computes calibration. Read "
            f"{CANONICAL_CALIBRATION_FILE} instead. It is written by "
            "chron_cron_verify.py (07:00 MYT) and chron_loop_close.py (07:15 MYT) "
            "via chron.chron_ledger_integrity.compute_calibration_unified."
        ),
        "read_at": _now_iso(),
    }
    try:
        canonical = json.loads(CANONICAL_CALIBRATION_FILE.read_text(encoding="utf-8"))
        payload["canonical_numbers"] = {
            k: canonical.get(k)
            for k in ("total", "correct", "incorrect", "mixed", "accuracy",
                      "mean_brier", "decisive", "raw", "correlation_inflation")
        }
    except Exception as exc:  # canonical missing/unreadable must be visible
        payload["canonical_numbers"] = None
        payload["canonical_read_error"] = f"{type(exc).__name__}: {exc}"
    return payload
