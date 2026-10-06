#!/usr/bin/env python3
"""
chron_gut_override.py — GOL v1: the gut-override ledger and its calibration JOIN
════════════════════════════════════════════════════════════════════════════════
Implements `/root/AAA/instructions/gut-override-ledger-spec.md`
(F13_RATIFIED_CHAT 2026-10-03, plus the FI-008 amendment of 2026-10-04).

The question GOL answers:
    For decisions in domain D, when Arif's gut diverged from the model's
    recommendation, what was the actual hit rate — and does it beat the
    model's own hit rate on the same predictions?

DEVIATION FROM THE SPEC, declared rather than silent
    The spec places the recorder in `chron_prediction.py` and the query in
    `chron_calibration.py`. **`chron_calibration.py` does not exist** — measured
    2026-10-04; `compute_calibration` and `save_calibration` both live in
    `chron_prediction.py` (lines 614 and 645), which is already ~1,240 lines.
    So this is a focused module *inside the same chron package*, importing
    CHRON's canonical paths and verdict vocabulary rather than restating them.
    Everything the spec actually requires is preserved: one new file in
    `chron/data/`, the same JSONL JOIN-on-`prediction_id` shape as
    `verification_log.jsonl`, no new organ, no new dashboard, no new tool.

THE AMENDMENT IS ENFORCED IN CODE, NOT IN PROSE
    FI-008, 2026-10-04: `gut_read` must be the human's VERBATIM words (quoted)
    or empty. Agent-synthesized gut-reads are forbidden, because a fabricated
    gut_read launders the exact R-FILTER defect this ledger exists to catch.
    `record_gut_override()` therefore REJECTS a non-empty gut_read that is not
    wrapped in quotation marks, and offers a separate, explicitly
    non-authoritative field for paraphrase. A doctrine that only exists in a
    markdown file is a preference; this one returns an error.

Why this module exists at all: an instrument nobody runs is indistinguishable
from one that does not exist. GOL is wired into `chron_cron_verify.py`, which
the `chron-prediction-verifier.timer` fires daily at 07:00 MYT (Persistent=true).

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Reuse CHRON's canonical paths + verdict vocabulary. Never restate them here:
# a second copy of a constant is a second source of truth free to drift.
from chron.chron_prediction import (
    PREDICTIONS_FILE,
    VERIFICATION_LOG,
    VERDICT_CORRECT,
    VERDICT_INCORRECT,
    _DECISIVE,
    _canonical_verdict,
)

GUT_OVERRIDES_FILE = Path("/root/chron/data/gut_overrides.jsonl")

MYT = timezone(timedelta(hours=8))

# ── Schema (spec §"Field constraints") ─────────────────────────────
ESSENTIAL_FIELDS = (
    "domain", "evidence", "hermes_recommended",
    "arif_did", "gut_read", "outcome_state",
)
OUTCOME_STATES = ("pending", "in_progress", "proven", "proven_songsang", "unresolvable")
# Only closed outcomes can contribute a hit rate. `in_progress` is a direction,
# not a result, and counting it would let an unfinished decision score as a win.
CLOSED_OUTCOMES = ("proven", "proven_songsang")
DOMAINS = ("PETRONAS", "lifestyle", "community", "family", "identity", "SADO", "other")

# ── Domain normalisation (2026-10-04, FI-003, F13 order) ───────────────────
# Measured on the live ledger: 5 rows produced 5 singleton buckets, 4 of them
# off-enum — 'PETRONAS exit', 'delivery format', 'family (sibling)',
# 'identity/representation'. Free-text domain silently fragments stratification,
# and at one row per bucket the spec's n>=30 Rule-0 threshold is unreachable, so
# gut_vs_corpus_edge would stay `inconclusive` forever no how many overrides were
# recorded. The instrument would look live and never produce a number.
#
# Fix: collapse to the closed enum on WRITE, and keep the original in
# `domain_raw`. Normalisation must not destroy the distinction it collapses —
# without domain_raw a future re-stratification (e.g. splitting SADO back out of
# family) is impossible and the ledger is permanently lossy.
#
# One owner: both writers (this module and AAA/scripts/gol_intake.py) call
# normalise_domain(). Two copies of an alias table is two sources of truth free
# to drift, which is the defect class this whole pass exists to remove.
DOMAIN_ALIASES = {
    # exact-match aliases, lower-cased for lookup
    "petronas": "PETRONAS",
    "petronas exit": "PETRONAS",
    "petronas/mss": "PETRONAS",
    "mss": "PETRONAS",
    "career": "PETRONAS",
    "work": "PETRONAS",
    "lifestyle": "lifestyle",
    "delivery format": "other",
    "delivery": "other",
    "format": "other",
    "community": "community",
    "networking": "community",
    "family": "family",
    "family (sibling)": "family",
    "family / lifestyle": "family",
    "family/lifestyle": "family",
    "family / sado": "SADO",
    "family/sado": "SADO",
    "sado": "SADO",
    "identity": "identity",
    "identity/representation": "identity",
    "identity / representation": "identity",
    "representation": "identity",
    "other": "other",
}


def normalise_domain(raw: Any) -> tuple[str, str | None]:
    """Collapse a free-text domain onto the closed enum.

    Returns (canonical, warning_or_None). Never raises and never invents a
    bucket: anything unrecognised falls to 'other' WITH a warning, so a new
    genuine domain surfaces as a prompt to extend the alias table rather than
    silently becoming its own singleton stratum.
    """
    s = str(raw or "").strip()
    if not s:
        return "other", "domain empty — filed under 'other'"
    if s in DOMAINS:
        return s, None

    key = s.lower()
    if key in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[key], None

    # Strip a parenthetical qualifier, then try slash-separated tokens. Both are
    # how the seed data actually wrote domains; guessing further would invent
    # structure the author did not assert.
    stripped = key.split("(")[0].strip()
    if stripped in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[stripped], None
    for tok in (t.strip() for t in stripped.split("/")):
        if tok in DOMAIN_ALIASES:
            return DOMAIN_ALIASES[tok], None
        if tok in {d.lower() for d in DOMAINS}:
            return next(d for d in DOMAINS if d.lower() == tok), None

    return "other", (
        f"domain {s!r} is not in the enum or alias table — filed under 'other'. "
        f"If this is a real new domain, extend DOMAIN_ALIASES; otherwise it will "
        f"fragment stratification and n>=30 will never be reached."
    )

# Spec §"Hard rule": below n=30 per domain the edge is inconclusive. No claim
# is publishable, every row is still data.
MIN_N_FOR_EDGE = 30

# GOL rows are sovereign-calibration data about Arif's own decisions, several of
# which concern F5-sealed personal lanes. Default audience is 'arif' so nothing
# here can surface on a two-person or shared surface by accident.
DEFAULT_AUDIENCE = "arif"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_verbatim_quote(text: str) -> bool:
    """The amendment's mechanical test: a quotation, or nothing."""
    t = (text or "").strip()
    if not t:
        return True                      # empty is explicitly permitted
    return (t.startswith('"') and t.endswith('"') and len(t) > 2) or \
           (t.startswith('\u201c') and t.endswith('\u201d') and len(t) > 2)


def load_overrides() -> list[dict]:
    if not GUT_OVERRIDES_FILE.exists():
        return []
    out: list[dict] = []
    with open(GUT_OVERRIDES_FILE, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                # A corrupt line must not silently vanish from the calibration
                # denominator — that would inflate every hit rate computed here.
                out.append({"_corrupt_line": ln[:200]})
    return out


def record_gut_override(
    prediction_id: str | None,
    gut_override: dict[str, Any],
    recorded_by: str = "arif",
    audience: str = DEFAULT_AUDIENCE,
) -> dict:
    """Validate and append one GOL row. Append-only, like every CHRON ledger.

    Returns {"recorded": True, "override_id": ...} or {"recorded": False,
    "error": ...}. Validation failures never write a partial row.
    """
    missing = [f for f in ESSENTIAL_FIELDS if f not in gut_override]
    if missing:
        return {"recorded": False, "error": f"missing essential field(s): {missing}"}

    outcome = str(gut_override.get("outcome_state", "")).strip()
    if outcome not in OUTCOME_STATES:
        return {"recorded": False,
                "error": f"outcome_state {outcome!r} not in {OUTCOME_STATES}"}

    if not str(gut_override.get("evidence", "")).strip():
        return {"recorded": False,
                "error": "evidence is empty — F2 TRUTH requires message refs/dates;"
                         " without it no override is real"}

    gut_read = str(gut_override.get("gut_read", ""))
    if not _is_verbatim_quote(gut_read):
        return {"recorded": False,
                "error": "gut_read must be the human's VERBATIM words in quotation "
                         "marks, or empty (FI-008 amendment 2026-10-04). An "
                         "agent-synthesized gut_read launders the exact defect this "
                         "ledger exists to catch. Put paraphrase in "
                         "gut_read_agent_paraphrase instead — it is retained but "
                         "never scored."}

    # Domain is normalised on WRITE, not at query time. Normalising only in the
    # calibration query would leave the ledger itself fragmented, so any other
    # reader (jq, a future dashboard, an audit) would still see five singleton
    # buckets. domain_raw is preserved so the collapse is reversible.
    domain_raw = str(gut_override.get("domain", "")).strip()
    domain, warn = normalise_domain(domain_raw)

    # ── LIVE WIRING (2026-10-04, FI-003, F13 "wire live overrides to real
    # predictions") ──
    # An override with no prediction_id can never contribute to
    # gut_vs_corpus_edge, so the ledger would collect rows forever and never
    # produce a number — all 5 seed rows are in exactly that state. Where a row
    # carries enough to falsify the model's own recommendation, mint a real
    # CHRON prediction through CHRON's own create_prediction() (never a
    # hand-rolled row, which would drift from the store's schema and skip its
    # calibrator) and JOIN to that.
    #
    # Semantics: the minted prediction is the MODEL's claim — "the recommended
    # action would have produced this outcome". The override row records the
    # HUMAN's counter-action and its outcome. gut_vs_corpus_edge is then the
    # difference between two verdicts about the SAME decision, which is the only
    # comparison that means anything.
    link = link_or_mint_prediction(prediction_id, gut_override)
    prediction_id = link["prediction_id"]

    row = {
        "override_id": f"gol-{uuid.uuid4().hex[:16]}",
        "prediction_id": prediction_id,
        # A row with no prediction_id cannot JOIN, so it can never contribute to
        # gut_vs_corpus_edge. Flagged at write time rather than discovered later
        # as a mysteriously empty denominator.
        "joinable": link["joinable"],
        "join_status": link["status"],
        "domain": domain,
        "domain_raw": domain_raw,
        "evidence": gut_override["evidence"],
        "hermes_recommended": gut_override["hermes_recommended"],
        "arif_did": gut_override["arif_did"],
        "gut_read": gut_read,
        # A1 satisfaction marker. gol_intake.py's validator requires exactly this
        # value whenever gut_read is non-empty. Without it the two writers to this
        # ledger produce rows that FAIL EACH OTHER'S GATES — measured 2026-10-04:
        # my first live row was rejected by `gol_intake.py --verify` with
        # "gut_read non-empty but gut_read_provenance != 'human_verbatim'".
        # A shared file with two validators that disagree is not one ledger.
        "gut_read_provenance": ("human_verbatim" if gut_read.strip() else "empty"),
        # Carried through so the row stays compatible with the intake gate's
        # schema and so the mint inputs survive for audit. Explicit list, not a
        # blanket copy of the caller's dict: several rows touch F5-sealed lanes,
        # so an unvetted passthrough field could carry a third-party name past
        # the A2 blocklist, which only scans known fields' values.
        "case_id": gut_override.get("case_id"),
        "model_expected_outcome": gut_override.get("model_expected_outcome", ""),
        "model_confidence": gut_override.get("model_confidence"),
        "gut_read_agent_paraphrase": gut_override.get("gut_read_agent_paraphrase", ""),
        "outcome_state": outcome,
        "outcome_close_date": gut_override.get("outcome_close_date"),
        "confidence": str(gut_override.get("confidence", "LOW")).upper(),
        "post_hoc_self_report": bool(gut_override.get("post_hoc_self_report", True)),
        "provenance": gut_override.get("provenance", "live"),
        "audience": audience,
        "recorded_at": _now_iso(),
        "recorded_by": recorded_by,
    }

    GUT_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GUT_OVERRIDES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    out = {"recorded": True, "override_id": row["override_id"],
           "joinable": row["joinable"], "written_to": str(GUT_OVERRIDES_FILE)}
    if warn:
        out["warning"] = warn
    return out


def _index_jsonl(path: Path, key: str) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    if not path.exists():
        return idx
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            k = r.get(key)
            if k:
                idx.setdefault(str(k), []).append(r)
    return idx


def calibration_by_gut_override() -> dict:
    """The JOIN: gut_overrides × predictions × verification_log on prediction_id.

    gut_hit_rate      = proven / (proven + proven_songsang), per domain
    corpus_hit_rate   = VERIFIED_CORRECT / decisive verdicts on the SAME joined
                        predictions — the model's own record where a human
                        overrode it
    gut_vs_corpus_edge = gut_hit_rate - corpus_hit_rate
                         positive  -> the human is systematically right to override
                         negative  -> the model was right; the override cost
                         ~zero    -> no signal, override is noise
    Anything below MIN_N_FOR_EDGE is reported `inconclusive`. Never as a number.
    """
    overrides = [o for o in load_overrides() if "_corrupt_line" not in o]
    corrupt = sum(1 for o in load_overrides() if "_corrupt_line" in o)
    preds = _index_jsonl(PREDICTIONS_FILE, "prediction_id")
    vlog = _index_jsonl(VERIFICATION_LOG, "prediction_id")

    per_domain: dict[str, dict] = {}
    joined = unjoinable = 0

    for o in overrides:
        dom = o.get("domain") or "other"
        b = per_domain.setdefault(dom, {
            "rows": 0, "closed": 0, "gut_hits": 0, "joinable": 0,
            "corpus_decisive": 0, "corpus_hits": 0, "unjoinable": 0,
        })
        b["rows"] += 1

        if o.get("outcome_state") in CLOSED_OUTCOMES:
            b["closed"] += 1
            if o["outcome_state"] == "proven":
                b["gut_hits"] += 1

        pid = o.get("prediction_id")
        if not pid or pid not in preds:
            b["unjoinable"] += 1
            unjoinable += 1
            continue
        joined += 1
        b["joinable"] += 1

        for rec in vlog.get(str(pid), []):
            v = _canonical_verdict(rec)
            if v in _DECISIVE:
                b["corpus_decisive"] += 1
                if v == VERDICT_CORRECT:
                    b["corpus_hits"] += 1

    domains = {}
    for dom, b in sorted(per_domain.items()):
        gut_rate = (b["gut_hits"] / b["closed"]) if b["closed"] else None
        corpus_rate = (b["corpus_hits"] / b["corpus_decisive"]) if b["corpus_decisive"] else None
        edge = (gut_rate - corpus_rate) if (gut_rate is not None and corpus_rate is not None) else None
        sufficient = b["closed"] >= MIN_N_FOR_EDGE and b["corpus_decisive"] >= MIN_N_FOR_EDGE
        domains[dom] = {
            **b,
            "gut_hit_rate": round(gut_rate, 4) if gut_rate is not None else None,
            "corpus_hit_rate": round(corpus_rate, 4) if corpus_rate is not None else None,
            "gut_vs_corpus_edge": round(edge, 4) if edge is not None else None,
            "verdict": "MEASURED" if sufficient else "inconclusive",
            "n_required": MIN_N_FOR_EDGE,
        }

    return {
        "generated_at": _now_iso(),
        "overrides_total": len(overrides),
        "corrupt_lines": corrupt,
        "joinable": joined,
        "unjoinable": unjoinable,
        "closed_outcomes": sum(b["closed"] for b in per_domain.values()),
        "min_n_for_edge": MIN_N_FOR_EDGE,
        "per_domain": domains,
        "note": ("edge is inconclusive below n=%d per domain on BOTH arms. Every row "
                 "is data; no claim is publishable early." % MIN_N_FOR_EDGE),
    }


def format_gut_report(cal: dict) -> str:
    """Human-readable block for the daily verifier cron."""
    lines = [
        "  Gut-Override Ledger (GOL):",
        f"    rows={cal['overrides_total']} joinable={cal['joinable']} "
        f"unjoinable={cal['unjoinable']} closed_outcomes={cal['closed_outcomes']}"
        + (f" CORRUPT_LINES={cal['corrupt_lines']}" if cal["corrupt_lines"] else ""),
    ]
    if not cal["per_domain"]:
        lines.append("    no rows yet — the ledger exists but nothing has been recorded.")
        return "\n".join(lines)
    for dom, b in cal["per_domain"].items():
        gr = "n/a" if b["gut_hit_rate"] is None else f"{b['gut_hit_rate']:.3f}"
        cr = "n/a" if b["corpus_hit_rate"] is None else f"{b['corpus_hit_rate']:.3f}"
        eg = "n/a" if b["gut_vs_corpus_edge"] is None else f"{b['gut_vs_corpus_edge']:+.3f}"
        lines.append(
            f"    {dom:<24} rows={b['rows']:<3} closed={b['closed']:<3} "
            f"gut_hit={gr:<6} corpus_hit={cr:<6} edge={eg:<8} {b['verdict']}"
        )
    if cal["unjoinable"]:
        lines.append(
            f"    ⚠ {cal['unjoinable']} row(s) have no joinable prediction_id — they can "
            f"never contribute to edge. Retrospective seed rows are expected here."
        )
    return "\n".join(lines)


# Prose → enum for the retrospective seed, whose outcome_state was written as
# free text before the enum existed.
_SEED_OUTCOME_MAP = {
    "in progress": "in_progress",
    "proven": "proven",
    "pending": "pending",
    "proven_songsang": "proven_songsang",
    "unresolvable": "unresolvable",
}


def _normalise_outcome(raw: str) -> str:
    s = (raw or "").strip().lower()
    for needle, enum in _SEED_OUTCOME_MAP.items():
        if s.startswith(needle):
            return enum
    return "pending"


def seed_from_json(path: str | Path, recorded_by: str = "seed-loader") -> dict:
    """Load the retrospective seed WITHOUT laundering agent prose into gut_read.

    The seed's five `gut_read` values are agent paraphrase, not Arif's words
    (measured 2026-10-04: none is quoted). The FI-008 amendment forbids exactly
    that, so each is moved to `gut_read_agent_paraphrase` — retained for context,
    excluded from scoring — and `gut_read` is left empty. Every seed row is
    marked `provenance: retrospective_seed_pre_deployment` and
    `post_hoc_self_report: True`, because the spec is explicit that GOL can never
    be retroactive to before its deployment; the seed is the boundary marker.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    cases = data if isinstance(data, list) else (
        data.get("cases") or data.get("overrides") or next(iter(data.values()))
    )

    recorded, rejected, emptied = 0, [], 0
    for c in cases:
        gut = str(c.get("gut_read", "") or "")
        if gut and not _is_verbatim_quote(gut):
            c = dict(c)
            c["gut_read_agent_paraphrase"] = gut
            c["gut_read"] = ""
            emptied += 1
        c.setdefault("provenance", "retrospective_seed_pre_deployment")
        c["post_hoc_self_report"] = True
        c["outcome_state"] = _normalise_outcome(str(c.get("outcome_state", "")))
        res = record_gut_override(
            prediction_id=c.get("prediction_id"),
            gut_override=c,
            recorded_by=recorded_by,
        )
        if res.get("recorded"):
            recorded += 1
        else:
            rejected.append({"domain": c.get("domain"), "error": res.get("error")})

    return {"seeded": recorded, "rejected": rejected,
            "gut_read_emptied_by_amendment": emptied, "source": str(p)}


# ── Live wiring: override → real CHRON prediction ──────────────────────────
def _known_prediction_ids() -> set[str]:
    ids: set[str] = set()
    if not PREDICTIONS_FILE.exists():
        return ids
    with open(PREDICTIONS_FILE, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                pid = json.loads(ln).get("prediction_id")
            except json.JSONDecodeError:
                continue
            if pid:
                ids.add(str(pid))
    return ids


# What a row must carry before a prediction can be minted for it. Without
# `model_expected_outcome` there is nothing to falsify — minting anyway would
# invent the model's claim, and a fabricated prediction is worse than no
# prediction because it later scores as if it had been real.
MINT_REQUIRES = ("hermes_recommended", "model_expected_outcome", "outcome_close_date")


def link_or_mint_prediction(prediction_id: str | None, gut_override: dict) -> dict:
    """Resolve an override onto a real prediction, minting one if needed.

    Returns {prediction_id, joinable, status}. Statuses:
      LINKED_EXISTING        caller supplied an id that exists in the store
      DANGLING_REFERENCE     caller supplied an id that does NOT exist — refused
                             rather than written, so a typo cannot silently
                             create an unjoinable row that looks joinable
      MINTED                 a new CHRON prediction was created for the model's
                             recommendation and linked
      UNJOINABLE_MISSING:x   the row lacks what minting needs; x names the fields
      MINT_FAILED:x          create_prediction raised; nothing half-written
    """
    known = _known_prediction_ids()

    if prediction_id:
        pid = str(prediction_id)
        if pid in known:
            return {"prediction_id": pid, "joinable": True, "status": "LINKED_EXISTING"}
        return {"prediction_id": None, "joinable": False,
                "status": f"DANGLING_REFERENCE:{pid}"}

    missing = [f for f in MINT_REQUIRES if not str(gut_override.get(f, "") or "").strip()]
    if missing:
        return {"prediction_id": None, "joinable": False,
                "status": "UNJOINABLE_MISSING:" + "+".join(missing)}

    try:
        from chron.chron_prediction import create_prediction
    except Exception as e:  # noqa: BLE001
        return {"prediction_id": None, "joinable": False,
                "status": f"MINT_FAILED:import:{type(e).__name__}"}

    close = str(gut_override["outcome_close_date"]).strip()[:10]
    try:
        pred = create_prediction(
            claim=f"GOL: the model's recommendation would have held — "
                  f"{str(gut_override['hermes_recommended'])[:280]}",
            expected_outcome=str(gut_override["model_expected_outcome"])[:400],
            confidence=float(gut_override.get("model_confidence", 0.5) or 0.5),
            verify_at=f"{close}T23:59:59+08:00",
            extra={
                "origin": "gut_override_ledger",
                "gol_domain": gut_override.get("domain"),
                "human_overrode": True,
            },
            source="gut_overrides",
            source_id=str(gut_override.get("case_id") or gut_override.get("domain") or "gol"),
            audience=DEFAULT_AUDIENCE,
            success_rule=str(gut_override["model_expected_outcome"])[:280],
            falsification_rule="Observed outcome diverges from the model's stated "
                               "expectation for the action it recommended",
            created_by="chron_gut_override.link_or_mint_prediction",
            trigger="gol_live_override",
        )
    except Exception as e:  # noqa: BLE001
        return {"prediction_id": None, "joinable": False,
                "status": f"MINT_FAILED:{type(e).__name__}: {e}"}

    pid = (pred or {}).get("prediction_id")
    if not pid:
        return {"prediction_id": None, "joinable": False,
                "status": "MINT_FAILED:no_prediction_id_returned"}
    return {"prediction_id": str(pid), "joinable": True, "status": "MINTED"}


def backfill_links(dry_run: bool = True) -> dict:
    """Report which existing rows could be linked, and exactly what each lacks.

    Dry-run by default: rewriting rows another lane authored, in a ledger whose
    whole purpose is calibration integrity, is not something to do silently.
    """
    rows = [r for r in load_overrides() if "_corrupt_line" not in r]
    known = _known_prediction_ids()
    report = {"rows": len(rows), "already_joinable": 0, "linkable_now": [],
              "blocked": [], "dry_run": dry_run}
    for r in rows:
        oid = r.get("override_id") or r.get("case_id") or "?"
        pid = r.get("prediction_id")
        if pid and str(pid) in known:
            report["already_joinable"] += 1
            continue
        missing = [f for f in MINT_REQUIRES if not str(r.get(f, "") or "").strip()]
        if missing:
            report["blocked"].append({"row": oid, "domain": r.get("domain"),
                                      "missing": missing})
        else:
            report["linkable_now"].append({"row": oid, "domain": r.get("domain")})
    return report
