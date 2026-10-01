#!/usr/bin/env python3
"""BL02 fail-closed contract tests (F13 order 2026-10-01, trc-20261001-fi003-bl02-failclosed).

Runs bl02_calibration_curves.py as a subprocess against temp fixtures via
BL02_STORE / BL02_OUT. No production file is read into or written out of the
federation stores.

Provable contract:
  1. absence of confidence  -> mean_confidence null, bias null, no penalty
  2. absence of brier       -> mean_brier null (never an invented 0.25)
  3. measured overconfidence STILL triggers REDUCE_WEIGHT (law not disabled)
  4. partial measured coverage below n>=8 cannot penalise
  5. reliability bins contain only measured-confidence rows
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = "/root/scripts/bl02_calibration_curves.py"
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  -> ' + detail}")
    if not cond:
        FAILS.append(name)


def run(rows):
    d = tempfile.mkdtemp(prefix="bl02test-")
    store, out = Path(d) / "predictions.jsonl", Path(d) / "out.json"
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    env = dict(os.environ, BL02_STORE=str(store), BL02_OUT=str(out))
    p = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-600:]
    return json.loads(out.read_text())


def row(actor, conf=None, brier=None, correct=False):
    r = {"prediction_id": f"p-{actor}-{len(str(conf))}-{correct}", "created_by": actor,
         "claim": "c", "status": "VERIFIED", "verified_at": "2026-09-30T00:00:00Z"}
    if conf is not None:
        r["confidence"] = conf
    if brier is not None:
        r["brier_score"] = brier
        r["verdict"] = "VERIFIED_CORRECT" if correct else "VERIFIED_INCORRECT"
    else:
        r["verdict"] = "VERIFIED_CORRECT" if correct else "VERIFIED_INCORRECT"
    return r


print("T1  n>=8, confidence entirely absent")
res = run([row("ghost", conf=None, brier=None, correct=(i < 7)) for i in range(8)])
c = res["per_actor"]["ghost"]
check("mean_confidence is null", c["mean_confidence"] is None, str(c["mean_confidence"]))
check("bias_overconfidence is null", c["bias_overconfidence"] is None, str(c["bias_overconfidence"]))
check("mean_brier is null (no invented 0.25)", c["mean_brier"] is None, str(c["mean_brier"]))
check("accuracy still measured", abs(c["accuracy"] - 0.875) < 1e-6, str(c["accuracy"]))
check("no penalty from invented data", not c["routing_advisory_bl12"].startswith("REDUCE_WEIGHT"),
      c["routing_advisory_bl12"])
check("advisory names the real cause", c["routing_advisory_bl12"] == "NOMINAL_INSUFFICIENT_CONFIDENCE",
      c["routing_advisory_bl12"])
check("confidence_source absent", c["confidence_source"] == "absent", c["confidence_source"])
check("quality INSUFFICIENT", c["quality"] == "INSUFFICIENT", c["quality"])
check("bins hold only measured rows", res["reliability_bins"] == {}, str(res["reliability_bins"]))
check("excluded count reported", res["fail_closed"]["rows_without_confidence_excluded_from_bins"] == 8,
      str(res["fail_closed"]))

print("\nT2  n>=8 with MEASURED confidence 0.9 and zero correct -> law must still fire")
res = run([row("loud", conf=0.9, brier=0.81, correct=False) for _ in range(8)])
c = res["per_actor"]["loud"]
check("VERIFIED_INCORRECT is scored as wrong (substring bug locked out)",
      c["accuracy"] == 0.0, str(c["accuracy"]))
check("mean_confidence measured", c["mean_confidence"] == 0.9, str(c["mean_confidence"]))
check("bias computed", abs(c["bias_overconfidence"] - 0.9) < 1e-6, str(c["bias_overconfidence"]))
check("REDUCE_WEIGHT issued on real evidence",
      c["routing_advisory_bl12"].startswith("REDUCE_WEIGHT"), c["routing_advisory_bl12"])
check("quality EMERGING", c["quality"] == "EMERGING", c["quality"])
check("bin placed at 90-100", "90-100" in res["reliability_bins"], str(res["reliability_bins"]))

print("\nT2b  mixed CORRECT / INCORRECT verdicts must not both count as correct")
res = run([row("mix", conf=0.9, brier=0.09, correct=True) for _ in range(4)] +
          [row("mix", conf=0.9, brier=0.81, correct=False) for _ in range(4)])
c = res["per_actor"]["mix"]
check("accuracy is exactly 0.5, not 1.0", c["accuracy"] == 0.5, str(c["accuracy"]))
check("bias = 0.9 - 0.5 = 0.4", abs(c["bias_overconfidence"] - 0.4) < 1e-6, str(c["bias_overconfidence"]))

print("\nT3  n=10 but only 5 measured confidence -> coverage too thin to penalise")
rows = ([row("half", conf=0.9, brier=0.81, correct=False) for _ in range(5)] +
        [row("half", conf=None, brier=None, correct=False) for _ in range(5)])
c = run(rows)["per_actor"]["half"]
check("effective_n counts all rows", c["effective_n"] == 10, str(c["effective_n"]))
check("n_confidence_observed counts only measured", c["n_confidence_observed"] == 5, str(c))
check("no penalty on partial coverage", not c["routing_advisory_bl12"].startswith("REDUCE_WEIGHT"),
      c["routing_advisory_bl12"])
check("source marked partial", c["confidence_source"] == "partial", c["confidence_source"])

print("\nT4  schema + provenance fields")
res = run([row("s", conf=0.5, brier=0.25, correct=True)])
check("schema bumped to v3", res["schema"] == "bl02.per_actor.v3", res["schema"])
check("fail_closed block present", "fail_closed" in res, str(list(res)))
check("synthetic defaults explicitly false",
      res["per_actor"]["s"]["synthetic_defaults_applied"] is False, "")
check("supersedes names v1", "v1" in res["fail_closed"]["supersedes"], res["fail_closed"]["supersedes"])

print("\nT5  retired claim must not survive as live doctrine")
src = Path(SCRIPT).read_text()
check("docstring marks +0.088 as RETRACTED", "RETRACTED" in src, "")
check("no 'or 0.5' default left in accumulation", "or 0.5" not in src.split("curves = {}")[0], "")
check("no 'or 0.25' default left", 'p.get("brier_score") or 0.25' not in src, "")

print("\nT6 attribution: instrument-self-reference and audience tokens never become actors")
rows = ([row("chron_prediction.generate_from_chron_events", conf=0.9, brier=0.81, correct=False)
         for _ in range(4)] +
        [dict(row("system", conf=0.9, brier=0.81, correct=False)) for _ in range(3)] +
        [row("grok-build", conf=0.9, brier=0.81, correct=False) for _ in range(2)])
res = run(rows)
pa = res["per_actor"]
check("pseudo-actors collapsed into one UNATTRIBUTED row", "UNATTRIBUTED" in pa, str(list(pa)))
check("unattributed row is NOT routable", pa["UNATTRIBUTED"]["routable"] is False, "")
check("unattributed row declares no routing keys", pa["UNATTRIBUTED"]["routing_keys"] == [], "")
check("advisory cannot be a penalty",
      pa["UNATTRIBUTED"]["routing_advisory_bl12"] == "NOMINAL_UNATTRIBUTED",
      pa["UNATTRIBUTED"]["routing_advisory_bl12"])
kinds = pa["UNATTRIBUTED"]["observer_kinds"]
check("reasons itemised (self_instrument vs anonymous)",
      kinds.get("self_instrument") == 4 and kinds.get("anonymous") == 3, str(kinds))
check("a real observer still gets its own routable row",
      pa.get("grok-build", {}).get("routable") is True, str(list(pa)))
check("attribution block forbids principal fallback",
      "FORBIDDEN" in res["attribution"]["principal_fallback"], str(res["attribution"])[:160])
check("a principal of arif on every row never becomes an actor key",
      "arif" not in pa, str(list(pa)))

print("\nT7 lane sub-vehicle rolls up to its lane root in declared keys")
res = run([row("333-AGI/FI-003", conf=0.9, brier=0.81, correct=False) for _ in range(2)])
key = "333-agi/fi-003"
check("key case-folded by the producer", key in res["per_actor"], str(list(res["per_actor"])))
check("routing_keys carry both the actor and its lane root",
      res["per_actor"][key]["routing_keys"] == ["333-agi", "333-agi/fi-003"],
      str(res["per_actor"][key]["routing_keys"]))

print("\nT8 confidence channels: payload is consumed, the witness constant is NOT")


def prow(actor, conf=None, session="live", **extra):
    r = {"prediction_id": f"p-{actor}-{conf}", "created_by": actor, "claim": "c",
         "status": "VERIFIED", "verified_at": "2026-09-30T00:00:00Z",
         "session_id": session, "verdict": "VERIFIED_INCORRECT"}
    pl = dict(extra)
    if conf is not None:
        pl["confidence"] = conf
    if pl:
        r["payload"] = pl
    return r


res = run([prow("qwen-code", conf=0.9) for _ in range(8)])
c = res["per_actor"]["qwen-code"]
check("payload-carried confidence is consumed (channel=payload)",
      c["n_confidence_observed"] == 8 and c["confidence_channels"] == {"payload": 8}, str(c))
check("accuracy honest (INCORRECT not counted as correct)", c["accuracy"] == 0.0, str(c["accuracy"]))
check("REDUCE_WEIGHT now reachable from a real payload stamp",
      c["routing_advisory_bl12"].startswith("REDUCE_WEIGHT"), c["routing_advisory_bl12"])

res = run([prow("qwen-code", conf=1.7) for _ in range(8)])
check("out-of-range payload confidence rejected",
      res["per_actor"]["qwen-code"]["n_confidence_observed"] == 0,
      str(res["per_actor"]["qwen-code"]["n_confidence_observed"]))

res = run([prow("qwen-code", conf=None, human=0.42, ai=0.32, earth=0.26) for _ in range(8)])
check("witness-style keys are NOT harvested as confidence",
      res["per_actor"]["qwen-code"]["n_confidence_observed"] == 0,
      str(res["per_actor"]["qwen-code"]))

res = run([prow("qwen-code", conf=0.9, session="e2e") for _ in range(8)])
check("test-session rows never accrue against a real actor",
      "qwen-code" not in res["per_actor"] and "UNATTRIBUTED" in res["per_actor"],
      str(list(res["per_actor"])))
check("and are itemised as test_residue",
      res["per_actor"]["UNATTRIBUTED"]["observer_kinds"].get("test_residue") == 8,
      str(res["per_actor"]["UNATTRIBUTED"]["observer_kinds"]))

print("\n" + ("ALL BL02 FAIL-CLOSED TESTS PASSED" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
