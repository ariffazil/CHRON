#!/usr/bin/env python3
"""CHRON Resolution-Contract Audit — read-only.

Answers three questions per prediction:
  1. Is there a success criterion and a failure criterion fixed at birth?
  2. Is there provenance and a resolver (machine probe or named human)?
  3. When can a decision legitimately be made?

It does NOT verify anything and does NOT mutate the store. It reports which
predictions are auditable, which are human-witness only, which are unbound,
and which are incomplete.

Decision-time semantics:
  - verify_at is the OUTCOME deadline, not the decision time.
  - An event discovered after the deadline can still satisfy the claim; the
    later date is when CHRON knows, not when reality changed.
  - Absence claims require full-window coverage; a first counterexample closes
    them immediately, but no counterexample + missing coverage is UNVERIFIABLE.
  - Threshold claims resolve at the specified measurement timestamp, not at
    the deadline.
  - Human-witness claims resolve when the named human confirms.

Usage:
  cd /root/chron && PYTHONPATH=/root python3 chron_contract_audit.py
  cd /root/chron && PYTHONPATH=/root python3 chron_contract_audit.py --json
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

PREDICTIONS = Path("/root/chron/data/predictions.jsonl")
VERIFICATIONS = Path("/root/chron/data/verification_log.jsonl")
OUT = Path("/root/chron/data/contract_audit.json")

ABSENCE_RE = re.compile(r"\b(no|not|zero|absent|never|without|fails? to)\b", re.I)
THRESHOLD_RE = re.compile(
    r"(>=|<=|>|<|≥|≤|\babove\b|\bbelow\b|\bat least\b|\bat most\b|\bbetween\b)", re.I
)
EVENT_RE = re.compile(r"^(event\s*:|.*\bwill (occur|happen|be)\b)", re.I)
GENERIC_SUCCESS_RE = re.compile(r"^Event '.*' occurs by ", re.I)
GENERIC_FAILURE_RE = re.compile(r"^Event '.*' does not occur by ", re.I)


def _s(r: dict) -> str:
    return (r.get("success_rule") or r.get("verifier_method") or "").strip()


def _f(r: dict) -> str:
    return (r.get("falsification_rule") or r.get("falsifier") or "").strip()


def _provenance(r: dict) -> bool:
    return bool(r.get("source_refs") or r.get("evidence_snapshot"))


def _coverage(r: dict) -> bool:
    return bool(r.get("coverage") or r.get("coverage_window"))


def _claim_type(claim: str) -> str:
    if ABSENCE_RE.search(claim):
        return "ABSENCE"
    if THRESHOLD_RE.search(claim):
        return "THRESHOLD"
    if EVENT_RE.search(claim):
        return "EVENT"
    return "OTHER"


def _generic_contract(success: str, failure: str) -> bool:
    """True for the fallback 'Event ... occurs by DATE' rule.

    These are generated when an event has no predictions[] array. The specific
    evidence path may exist in the event note, but it is NOT encoded in the
    prediction birth record, so the decision is not reproducible from the
    record alone.
    """
    return bool(GENERIC_SUCCESS_RE.match(success) and GENERIC_FAILURE_RE.match(failure))


def classify(r: dict) -> dict:
    success, failure = _s(r), _f(r)
    deadline = r.get("verify_at")
    expected = r.get("expected_outcome")
    probe = r.get("probe_id")
    audience = r.get("audience")

    missing = [
        name
        for name, value in (
            ("success_criterion", success),
            ("failure_criterion", failure),
            ("deadline", deadline),
            ("expected_outcome", expected),
        )
        if not value
    ]

    if missing:
        claim = r.get("claim", "")
        claim_type = _claim_type(claim)
        return {
            "prediction_id": r.get("prediction_id"),
            "claim": claim,
            "contract_status": "INCOMPLETE",
            "resolver": "NONE",
            "claim_type": claim_type,
            "missing": missing,
            "decision_time": "UNDEFINED",
            "decision_rule": "add the missing criterion before calling this a forecast",
            "provenance": _provenance(r),
            "coverage_required": claim_type == "ABSENCE",
            "coverage_specified": _coverage(r),
            "verify_at": deadline,
        }

    claim = r.get("claim", "")
    claim_type = _claim_type(claim)
    coverage_specified = _coverage(r)

    # An absence claim ("zero failures", "will NOT", "never") cannot be
    # confirmed by the deadline alone. A first counterexample falsifies it
    # immediately; a YES requires evidence that the whole window was covered.
    # Without a declared coverage window the contract is incomplete even when
    # success/failure criteria exist.
    if claim_type == "ABSENCE" and not coverage_specified:
        return {
            "prediction_id": r.get("prediction_id"),
            "claim": claim,
            "contract_status": "INCOMPLETE_COVERAGE",
            "resolver": "MACHINE"
            if probe
            else ("HUMAN" if audience == "arif" else "NONE"),
            "claim_type": claim_type,
            "missing": ["coverage_window"],
            "decision_time": "UNDEFINED_COVERAGE",
            "decision_rule": "absence claim: first counterexample closes NO immediately; YES only after full-window coverage, which is not specified",
            "provenance": _provenance(r),
            "coverage_required": True,
            "coverage_specified": False,
            "verify_at": deadline,
        }

    if _generic_contract(success, failure):
        resolver = "MACHINE" if probe else ("HUMAN" if audience == "arif" else "NONE")
        return {
            "prediction_id": r.get("prediction_id"),
            "claim": claim,
            "contract_status": "GENERIC_CONTRACT",
            "resolver": resolver,
            "claim_type": claim_type,
            "missing": ["specific_evidence_criterion"],
            "decision_time": "AT_EVENT_EVIDENCE_OR_NOTE",
            "decision_rule": "generic event-occurrence rule; the specific evidence path is not encoded in the birth record — decision depends on the event note and is not reproducible from the prediction alone",
            "provenance": _provenance(r),
            "coverage_required": claim_type == "ABSENCE",
            "coverage_specified": _coverage(r),
            "verify_at": deadline,
        }

    if probe:
        resolver = "MACHINE"
        decision_time = "AT_PROBE_RESULT"
        decision_rule = "first probe result at/after verify_at; earlier if the event is observed early"
    elif audience == "arif":
        resolver = "HUMAN"
        decision_time = "AT_HUMAN_CONFIRMATION"
        decision_rule = "when the named human confirms the criterion; verify_at is a reminder, not a verdict"
    else:
        resolver = "NONE"
        decision_time = "NEVER_AUTOMATIC"
        decision_rule = "bind a machine probe or a named human witness"

    if claim_type == "ABSENCE":
        decision_rule += "; requires full-window coverage — a first counterexample closes it immediately, no counterexample without coverage stays UNVERIFIABLE"
    elif claim_type == "THRESHOLD":
        decision_rule += (
            "; decision at the specified measurement timestamp, not at the deadline"
        )
    elif claim_type == "EVENT":
        decision_rule += (
            "; decision when evidence of the event is available, even after verify_at"
        )

    return {
        "prediction_id": r.get("prediction_id"),
        "claim": claim,
        "contract_status": (
            "MACHINE_TESTABLE"
            if resolver == "MACHINE"
            else "HUMAN_WITNESS"
            if resolver == "HUMAN"
            else "UNBOUND"
        ),
        "resolver": resolver,
        "claim_type": claim_type,
        "missing": [],
        "decision_time": decision_time,
        "decision_rule": decision_rule,
        "provenance": _provenance(r),
        "coverage_required": claim_type == "ABSENCE",
        "coverage_specified": False,
        "verify_at": deadline,
    }


def audit() -> dict:
    rows = [
        json.loads(line)
        for line in PREDICTIONS.read_text().splitlines()
        if line.strip()
    ]
    items = [classify(r) for r in rows]

    # Verification provenance is a separate surface from prediction contracts.
    # Historical records predate the resolution block; report ABSENT rather
    # than imputing completeness.
    ver_rows = (
        [
            json.loads(line)
            for line in VERIFICATIONS.read_text().splitlines()
            if line.strip()
        ]
        if VERIFICATIONS.exists()
        else []
    )
    verification_provenance = Counter(
        (r.get("resolution") or {}).get("completeness", "ABSENT") for r in ver_rows
    )
    witness_status = Counter(r.get("witness_status", "ABSENT") for r in ver_rows)
    verification_times = Counter(
        (r.get("resolution") or {}).get("time_status", "ABSENT") for r in ver_rows
    )

    summary = {
        "total": len(items),
        "by_contract": dict(Counter(i["contract_status"] for i in items)),
        "by_resolver": dict(Counter(i["resolver"] for i in items)),
        "by_claim_type": dict(Counter(i["claim_type"] for i in items)),
        "with_provenance": sum(1 for i in items if i["provenance"]),
        "generic_contracts": sum(
            1 for i in items if i["contract_status"] == "GENERIC_CONTRACT"
        ),
        "coverage_required": sum(1 for i in items if i["coverage_required"]),
        "coverage_specified": sum(1 for i in items if i["coverage_specified"]),
        "decision_time": dict(Counter(i["decision_time"] for i in items)),
        "verification_rows": len(ver_rows),
        "verification_provenance": dict(verification_provenance),
        "witness_status": dict(witness_status),
        "verification_times": dict(verification_times),
    }
    report = {"summary": summary, "items": items}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> int:
    report = audit()
    s = report["summary"]
    print("CHRON RESOLUTION-CONTRACT AUDIT")
    print(json.dumps(s, indent=2))
    print("\nINCOMPLETE (first 10):")
    for i in [x for x in report["items"] if x["contract_status"] == "INCOMPLETE"][:10]:
        print(f"  {i['prediction_id']} | {', '.join(i['missing'])} | {i['claim'][:80]}")
    print("\nMACHINE_TESTABLE:")
    for i in [x for x in report["items"] if x["contract_status"] == "MACHINE_TESTABLE"]:
        print(f"  {i['prediction_id']} | {i['verify_at']} | {i['claim'][:80]}")
    print(f"\nreport: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
