"""Loop-closure invariants — proves the 2026-09-18 CHRON repair.

Each check pins one defect that was measured live before the fix. Nothing here
touches production data: every store path is redirected to a temp dir, and the
evidence lane is driven by in-process fixtures rather than the network.

Run: /opt/arifos/venv/bin/python3 /root/chron/tests/test_loop_closure.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root")

from chron import chron_learn as cl  # noqa: E402
from chron import chron_loop_close as lc  # noqa: E402
from chron import chron_prediction as cp  # noqa: E402
from chron import chron_verify as cv  # noqa: E402

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(label)


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def fixture(title: str, source: str, content: str) -> dict:
    """One search result, addressed by a local label rather than a live source."""
    return {"title": title, "url": source, "content": content}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chron-loop-test-"))
    preds = tmp / "predictions.jsonl"
    vlog = tmp / "verification_log.jsonl"
    attempts = tmp / "verification_attempts.jsonl"
    lessons = tmp / "lessons.jsonl"
    loopp = tmp / "loop_log.jsonl"
    preds.touch()
    vlog.touch()
    lessons.touch()

    # Redirect every store the modules touch.
    #
    # The episode store is redirected at its SOURCE module, not per-caller: the
    # chron modules do `from chron.chron_store import get_store` INSIDE functions,
    # so patching a caller's namespace leaves other paths writing to production.
    # A test that writes to production is not a test. (2026-09-18 — this file
    # polluted the real episode ledger before this line existed, because a later
    # section restored the real generate_from_chron_events.)
    import chron.chron_store as cs
    scratch_store = cs.ChronStore(tmp)
    cs.get_store = lambda *a, **k: scratch_store
    lc.get_store = lambda *a, **k: scratch_store

    cp.PREDICTIONS_FILE = preds
    cp.VERIFICATION_LOG = vlog
    cp.CALIBRATION_FILE = tmp / "calibration.json"
    cv.VERIFICATION_LOG = vlog
    cv.ATTEMPTS_LOG = attempts
    cl.LESSONS_FILE = lessons
    lc.LOOP_LOG = loopp

    # ── 1. V1/V2/V3: the verifier must not mutate birth records, must not write
    #         a non-canonical row, and must not turn silence into a verdict.
    print("\n1. Verifier immutability + no-claim-from-silence")
    preds.write_text(json.dumps({
        "prediction_id": "pred-x", "claim": "c", "expected_outcome": "e",
        "confidence": 0.5, "verify_at": "2020-01-01T00:00:00+08:00",
        "status": "ACTIVE", "principal": "arif",
    }) + "\n")
    before_preds, before_vlog = sha(preds), sha(vlog)

    real_events = cv.CHRON_EVENTS
    cv.CHRON_EVENTS = tmp / "no_events.json"  # source event absent -> UNVERIFIABLE
    res = cv.run_verification(dry_run=False)
    cv.CHRON_EVENTS = real_events

    check("no decisive verdict produced", res["verified_correct"] + res["verified_incorrect"], 0)
    check("counted unverifiable", res["unverifiable"], 1)
    check("predictions.jsonl UNCHANGED (immutability)", sha(preds), before_preds)
    check("verification_log.jsonl UNCHANGED (canonical-only)", sha(vlog), before_vlog)
    check("attempt recorded separately", attempts.exists(), True)
    check("calibration scores nothing", cp.compute_calibration()["total"], 0)
    check("prediction stays open", len(cp.get_active()), 1)

    # ── 2. V3 direct: a passed date is NOT a verdict.
    #    Guard the exact defect: the old verify_event_prediction returned
    #    error=0.0 ("CORRECT") for FISCAL/REGULATORY purely because the calendar
    #    date had passed. This uses a synthetic event whose date is in the past.
    print("\n2. Date passage is not evidence")
    synth = tmp / "events_synth.json"
    synth.write_text(json.dumps({"events": [{
        "id": "past-fiscal", "title": "Past fiscal event",
        "target_date": "2020-01-01", "kind": "FISCAL",
        "audience": "both", "confidence": "CONFIRMED",
    }]}))
    real_events2 = cv.CHRON_EVENTS
    cv.CHRON_EVENTS = synth
    p, observed, error, ecls = cv.verify_event_prediction(
        {"source_id": "past-fiscal", "unit": "", "threshold": None}, use_search=False
    )
    cv.CHRON_EVENTS = real_events2
    check("passed date yields error=None (open)", error, None)
    check("class is AWAITING_EVIDENCE, not NONE/CORRECT", ecls, "AWAITING_EVIDENCE")

    # ── 3. L1: a loop entry must carry BOTH delta and state.
    print("\n3. Loop entry carries delta AND state")
    lc.arrow_experience_to_memory = lambda dry_run=False: {
        "arrow": "EXPERIENCE → MEMORY",
        "bridge": {"episodes_created": 0}, "fq_poll": {"episodes_created": 0},
        "status": "COMPLETE",
    }
    lc.arrow_outcome_to_learning = lambda dry_run=False: {
        "arrow": "OUTCOME → LEARNING", "lessons_extracted": 0,
        "verify_result": {"verified_correct": 0, "verified_incorrect": 0},
        "status": "NO_DUE_PREDICTIONS",
    }
    lc._inject_carry_forward = lambda s: (True, None)
    import chron.chron_prediction as _cp
    orig_gen = _cp.generate_from_chron_events
    _cp.generate_from_chron_events = lambda: []

    r = lc.run_full_loop(dry_run=False)
    check("summary has delta", "delta" in r["summary"], True)
    check("summary has state", isinstance(r["summary"]["state"], dict), True)
    check("state reports store truth (active)", r["summary"]["state"]["predictions_active"], 1)
    check("status is QUIET when nothing changed", r["summary"]["status"], "QUIET")
    check("errors empty", r["summary"]["errors"], [])

    logged = [json.loads(l) for l in loopp.read_text().splitlines() if l.strip()]
    full = [e for e in logged if e["arrow"] == "FULL_LOOP"]
    check("FULL_LOOP entry written", len(full), 1)
    check("logged entry carries state", "state" in full[0]["data"], True)
    check("logged entry carries status", full[0]["data"]["status"], "QUIET")

    # ── 4. L2/L3: a failing arrow must surface, not vanish.
    print("\n4. Degradation is reported, not swallowed")
    def boom(dry_run=False):
        raise RuntimeError("bridge unreachable")
    lc.arrow_memory_to_policy = boom
    r2 = lc.run_full_loop(dry_run=False)
    check("status DEGRADED", r2["summary"]["status"], "DEGRADED")
    check("error captured", len(r2["summary"]["errors"]), 1)
    check("error names the step", r2["summary"]["errors"][0]["step"], "memory_to_policy")
    check("error carries the message", "bridge unreachable" in r2["summary"]["errors"][0]["error"], True)
    check("other arrows still ran", "outcome_to_learning" in r2["arrows"], True)

    # Keep the generator stubbed for the rest of the file. Restoring the real one
    # here is what let later sections write real predictions. (2026-09-18)
    _cp.generate_from_chron_events = lambda: []

    # ── 5. L4: the same lesson must not be appended twice.
    print("\n5. Lesson dedup")
    cl.get_verified = lambda limit=100: [{
        "prediction_id": "pred-y", "error_type": "DATA_ERROR",
        "brier_score": None, "principal": "arif", "claim": "c",
    }]
    cl.get_store = lambda: type("S", (), {"append": lambda self, e: None})()
    first = cl.extract_lessons()
    second = cl.extract_lessons()
    check("first pass yields the lesson", len(first), 1)
    check("second pass yields nothing", len(second), 0)
    check("lessons.jsonl holds exactly one row",
          len([l for l in lessons.read_text().splitlines() if l.strip()]), 1)
    check("fingerprint stored", "fingerprint" in first[0], True)

    # ── 6. Evidence lane must decline far more often than it decides, and must
    #      never let unrelated digits become a verdict.
    print("\n6. Evidence lane discipline")
    synth_event = {"id": "e1", "title": "Belanjawan 2027 dibentang di Parlimen",
                   "target_date": "2026-10-09", "kind": "FISCAL"}

    # (a) The regression found by probing: a page full of unrelated numbers must
    #     NOT produce a magnitude verdict. 2026 here is a year, not an amount.
    cv._search = lambda q, timeout=10: [
        fixture("portal year page", "fixture-portal-year",
                "Portal launched 2026, 30 services, 15 ministries listed")
    ]
    got = cv._evidence_for({"unit": "MYR", "threshold": 30000000000}, synth_event)
    check("magnitude claim with unrelated digits declines", got, None)

    # (b) A snippet that does not talk about the claim must not vote.
    cv._search = lambda q, timeout=10: [
        fixture("country overview page", "fixture-country-page",
                "Malaysia is a country in Southeast Asia. The government was announced.")
    ]
    got = cv._evidence_for({"unit": "", "threshold": None}, synth_event)
    check("off-topic snippet declines", got, None)

    # (c) A snippet that DOES speak to the claim, confirming it.
    cv._search = lambda q, timeout=10: [
        fixture("Belanjawan 2027 dibentang", "fixture-mof-b2027",
                "Belanjawan 2027 dibentang di Parlimen pada 9 Oktober 2026.")
    ]
    got = cv._evidence_for({"unit": "", "threshold": None}, synth_event)
    check("on-topic confirming snippet decides CORRECT", (got or {}).get("error"), 0.0)
    check("confirming class is NONE", (got or {}).get("error_class"), "NONE")
    check("source attached", bool((got or {}).get("source")), True)

    # (d) A snippet reporting a delay must decide INCORRECT, not stay silent.
    cv._search = lambda q, timeout=10: [
        fixture("Belanjawan 2027 ditangguh", "fixture-news-b2027",
                "Belanjawan 2027 dibentang di Parlimen ditangguh ke November.")
    ]
    got = cv._evidence_for({"unit": "", "threshold": None}, synth_event)
    check("on-topic denying snippet decides INCORRECT", (got or {}).get("error"), 1.0)
    check("denying class is REGIME_CHANGE", (got or {}).get("error_class"), "REGIME_CHANGE")

    # ── 7. Warnings: best-effort outbound steps must be disclosed, and must not
    #      be reported as errors (a dark receipt lane is not an organ failure).
    print("\n7. Best-effort steps are disclosed, not swallowed")
    lc._emit_ariflow_receipt = lambda data: (False, "arifFlow unreachable")
    lc.arrow_memory_to_policy = lambda dry_run=False: {
        "arrow": "MEMORY → POLICY", "promoted": 0, "held": 0,
        "lessons_total": 0, "candidates": 0, "status": "NO_PROMOTIONS",
    }
    lc._inject_carry_forward = lambda s: (False, "carry_forward missing")
    r3 = lc.run_full_loop(dry_run=False)
    warns = r3["summary"].get("warnings", [])
    steps = {w["step"] for w in warns}
    check("arifFlow failure disclosed", "arifflow_receipt" in steps, True)
    check("carry_forward failure disclosed", "carry_forward_inject" in steps, True)
    check("warnings do NOT set DEGRADED", r3["summary"]["status"] != "DEGRADED", True)

    logged3 = [json.loads(l) for l in loopp.read_text().splitlines() if l.strip()]
    last = [e for e in logged3 if e["arrow"] == "FULL_LOOP"][-1]
    check("warning survives into the written entry",
          "warnings" in last["data"] and bool(last["data"]["warnings"]), True)

    # ── 8. The episode delta must be MEASURED, not a sub-step's self-report.
    #      run_bridge() returns new_receipts/total_lines/offset and has no
    #      episodes_created key, so summing it was structurally always 0 while
    #      the store grew (observed live: 50470 -> 50505, delta reported 0).
    print("\n8. Episode delta is measured, not self-reported")
    counter = {"n": 100}
    class FakeStore:
        def count(self): return counter["n"]
        def functions(self): return {"observe": counter["n"]}
    lc.get_store = lambda *a, **k: FakeStore()
    lc._inject_carry_forward = lambda s: (True, None)
    lc._emit_ariflow_receipt = lambda d: (True, None)
    lc.arrow_memory_to_policy = lambda dry_run=False: {
        "arrow": "MEMORY → POLICY", "promoted": 0, "held": 0,
        "lessons_total": 0, "candidates": 0, "status": "NO_PROMOTIONS",
    }

    def e2m_that_writes(dry_run=False):
        counter["n"] += 3
        return {
            "arrow": "EXPERIENCE → MEMORY",
            # mimic the real bridge: no episodes_created key at all
            "bridge": {"status": "OK", "new_receipts": 5, "total_lines": 9},
            "fq_poll": {"status": "SKIP"},
            "status": "COMPLETE",
        }
    lc.arrow_experience_to_memory = e2m_that_writes

    r4 = lc.run_full_loop(dry_run=False)
    check("delta reflects the MEASURED gain (3)", r4["summary"]["delta"]["episodes_created"], 3)
    check("self-report kept as cross-check (bridge=0)",
          r4["summary"]["delta"]["episodes_created_reported"]["bridge"], 0)
    check("status ACTIVE when store grew", r4["summary"]["status"], "ACTIVE")

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} check(s): {FAILS}")
        return 1
    print("RESULT: PASS — loop-closure invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
