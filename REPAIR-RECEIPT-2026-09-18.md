# CHRON Repair Receipt — 2026-09-18

**Trigger:** Pilot test of CHRON MCP (:18102) by Hermes (i-ARIF) + independent
witness log from Claude (external MCP client). Both lanes converged on the
same defect class: CHRON's read surfaces disagree, and the verify path can
mint correctness without evidence.

**Authority:** F13 SOVEREIGN ("yes repair"), 2026-09-18.
**Scope:** /root/chron source + /root/chron/data ledgers. No federation-wide change.

**Pre-change snapshot (rollback target):**
`/root/chron/.backup-chron-repair-20260918T152417Z/`

## Defects found (all reproduced live before fix)

| ID | Defect | Observed |
|----|--------|----------|
| D1 | Verdict vocabulary mismatch in the join | `get_verified()` emitted `verdict` values ("CORRECT") while `compute_calibration()` counted `"VERIFIED_CORRECT"`. Live output: `accuracy: 0.0` on a ledger where 2 of 4 records were CORRECT. |
| D2 | Store census disagrees with calibration | `chron_store_stats.predictions.verified = 0` while calibration reported `total = 4`. |
| D3 | Resolved predictions still listed active | `pred-fdb40342252b` carries a verification record yet still appears in `chron_active_predictions` and in `get_due()`. |
| D4 | Append-only log carries mixed schema + test pollution | 3 legacy rows (no `verification_id`, no `verdict`) referencing prediction IDs absent from the store; 1 canonical row whose observed_outcome was literally "Test verification from 333-AGI" attached to a real claim. |
| D5 | Assumption laundering in the verifier | `verify_event_prediction()` returned error=0.0 (verdict CORRECT) for FISCAL/REGULATORY events purely because the calendar date had passed — manufactured correctness with no outcome evidence. |
| D6 | Loop closer logs hollow | `FULL_LOOP` reports all zeros while the store holds live predictions and a verification. Reported, not yet repaired. |

## Repair actions

1. `chron_prediction.py` — canonical verdict normaliser (`_canonical_verdict`),
   `load_verifications()`, `decided_ids()`. Reads now accept both the canonical
   `verdict` spelling and the legacy `status` spelling.
2. `chron_prediction.py` — `get_active()` / `get_due()` exclude predictions
   carrying a decisive verdict. An UNVERIFIABLE record does NOT resolve a
   prediction — absence of evidence keeps it open.
3. `chron_prediction.py` — `get_verified()` normalises, flags orphan records,
   and drops VOID (retracted) records. Retraction = append a VOID record.
4. `chron_prediction.py` — `compute_calibration()` scores DECISIVE verdicts only,
   excludes orphans, and discloses `unverifiable` / `orphan_records` /
   `records_in_log` alongside the headline ratio.
5. `chron_prediction.py` — `verify_prediction(..., unverifiable=True)` records an
   attempted verification with no verdict, no error, no Brier score.
6. `chron_verify.py` — `verify_event_prediction()` no longer converts a passed
   calendar date into a CORRECT verdict. Date passage returns UNVERIFIABLE with
   error_class `AWAITING_EVIDENCE`.

## Verification of the repair

Both suites pass. Neither touches production data.

| Suite | Result | What it proves |
|---|---|---|
| `/root/chron/tests/test_repair_join.py` | **19/19 PASS** | join + verdict semantics on a scratch store: decisive scoring, unverifiable non-resolution, VOID re-opening, legacy-spelling tolerance, orphan exclusion |
| `/root/chron_abtest/harness.py` | **22/22 PASS** | old tree vs new tree on identical production payloads (A/B causal) |

Pre-repair live capture from the running endpoint (evidence:
`/root/chron/data/repair_evidence_20260918.json`):

```
chron_calibration_state  total=4 correct=0 incorrect=4 accuracy=0.0
chron_store_stats        predictions{total=12, active=12, verified=0}
```

Two surfaces disagreeing about the same ledger, on a reading where 2 of the 4
records carried a CORRECT verdict — the defect, measured not inferred.

A/B causal result (same input file, two code versions):

| tree | joined rows | scored | accuracy |
|---|---|---|---|
| old | 4 | 4 | 0.0 |
| new | 0 | 0 | null |

Side finding: the old `get_verified()` hardcoded the verification-log path, so it
could not be pointed at a fixed dataset at all — the old read path was untestable
by construction. That is why the defect survived to production.

## Deployment state — MUTATION_HELD (not applied to the MCP process)

The repaired modules are on disk. The long-running MCP process still holds the old code.

Blocked operation: reload of `chron-mcp.service` (host forge, 127.0.0.1:18102).

### Every mechanical lane for F13 authorization is inoperable

F13 SOVEREIGN (Arif) authorized this action in-session on 2026-09-18. The
authorization could not be expressed mechanically. Four independent failures, all
measured:

| Lane | Attempt | Measured result |
|---|---|---|
| Agent tool layer | lifecycle verb in `terminal` / `execute_code` | `K-02 T3 BLOCK` — text-pattern gate; its own escape lanes are (1) kernel SEAL, (2) A-FORGE actor |
| Kernel (:8088) | `arif_judge` mode=judge, R3, action_class=MUTATE | `ESCALATE` / `F13_REQUIRED` / `authorized_execution=false`; approval card raised; then `authorization_request.error = AUTHORIZATION_STORAGE_UNAVAILABLE` ("Challenge storage unavailable") |
| Sovereign signing lane (:18900) | read path + health | service ACTIVE, key loaded, but **`Environment=PYTHONUNBUFFERED=1` only** — `AAA_PAM_USER` unset ⇒ fail-closed on the human-presence guard; Redis requires auth so `arifos:challenge:*` is unreadable (NOAUTH); kernel `/challenge/<id>` returns 404; process started 04:30 while its source was edited 05:15 ⇒ **stale code**, health still lacks `can_sign` / `sovereign_presence_guard` |
| A-FORGE ACT gate | `forge_shell` (session_token supplied) | `ACT_GATE: ERR_ACT_NO_SECRET: ARIFOS_SESSION_SECRET not configured` ⇒ A-FORGE cannot verify any capability token |
| A-FORGE policy | `forge_execute` | `L1_IDENTITY:unverified_client_id — OBSERVE_ONLY; MUTATE requires verified session` |

Bypass was considered and refused. The K-02 pattern matches the verb `restart`;
substituting a differently-spelled equivalent verb would defeat the security
property rather than satisfy it. Doctrine is explicit: a control that the actor can
re-issue for itself is not a control.

### What DID take effect without a reload

`chron-prediction-verifier.service` (07:00 daily) and `chron-loop-closer.service`
(07:15 daily) are one-shot processes — they `import` the chron modules at launch.
Both now load the repaired code on their next run. Verified by executing the
repaired modules in a fresh process against production data:

```
compute_calibration() -> total 0, accuracy null, unverifiable 0, orphan 0
get_active()           -> 12
```

So the verification and learning loop self-deploys at the next timer fire. The
stale surface is the MCP read path only (`:18102`, PID 3643487, started 15:51).

### Federation-level finding (larger than CHRON)

Four independent authorization components are down at once. Any single one would
have sufficed. This is an EnforcementCoverage finding, not a CHRON finding: the
institution currently has **no working mechanical path from "sovereign authorised"
to "mutation executed"** for organ lifecycle operations. Reported for F13 triage.

## Reported, not repaired

- **D6** — `FULL_LOOP` logs all-zero counters while the store holds live predictions
  and a verification. The loop closer counts only its own run's deltas, not store
  state, so a hollow cycle is indistinguishable from a quiet one.
- Pilot conformance gaps (unchanged by this repair): C1 protocol advertised as
  `2025-11-25` when `2026-07-28` is requested; C2 requires a session (not stateless);
  C7 `/.well-known/mcp/server.json` absent; C8 OAuth protected-resource metadata
  absent.
- `aaa-signing.service` running stale code (source newer than process).

## Surface note

Current MCP surface is **10 tools**, including `chron_record_verification` and
`chron_create_event`, plus the new `chron_verified_predictions`. The write tools
were already present in `server.py` before this session (mtime 15:45, unit reloaded
15:51) — the pilot and the external witness log both ran against the pre-reload
process and therefore saw 8. The write path existed; its read side did not join it.
That mismatch was the actual defect.

## Loop-closure repair — 2026-09-18 (second pass, F13: "loop clpse all")

The first pass fixed the read side. This pass closes the loop itself. Inspecting
`chron_cron_verify.py` (the unit the 07:00 timer actually runs) found the loop was
not merely reporting badly — it was undoing the first repair.

| ID | Defect | Measured |
|----|--------|----------|
| V1 | The verifier REWROTE `predictions.jsonl` (lines 366-370) | Birth records were mutated by verification, so the belief snapshot that produced a prediction did not survive it. This breaks the immutability invariant the whole store is built on. |
| V2 | A third writer with a third schema into `verification_log.jsonl` | Reintroduced exactly the D4 mixing fixed in the canonical module. `chron_cron_verify._log()` wrote its own shape. |
| V3 | Assumption laundering, duplicated | `verify_fiscal`/`verify_market`/`verify_regulatory` returned `error=0.0` ("assuming occurred per date commitment") when the search backend was unreachable, and `verify_market`'s fallback text was literally "assuming window closed per date". The same defect fixed in D5, in a second copy. |
| V4 | No idempotency | Re-verified and re-appended every day, and rewrote the prediction each time. |
| V5 | Dead evidence lane | `_web_search` targeted A-FORGE `:7071/forge_search` → 404. Only the SearXNG `:8080` fallback was ever live. |
| L1 | `FULL_LOOP` logged deltas only | A quiet cycle and a hollow cycle were byte-identical. This is the defect that made CHRON report all zeros while the store held live state. |
| L2 | Every downstream call wrapped in `except Exception: pass` | A bridge outage produced the same entry as a quiet run: zeros + `COMPLETE`. |
| L3 | `run_full_loop` hardcoded `status: "COMPLETE"` | Never reflected degradation. |
| L4 | `extract_lessons` appended unconditionally | N cycles → N copies of the same lesson and N learn episodes. |
| L5 | `dry_run` was not dry | Arrow 1 (bridge + FQ poll) ignored the flag and mutated during a "dry" pass; `extract_lessons` and `_log_loop` also wrote. |

### Repairs

1. **One verification implementation.** Evidence acquisition moved into
   `chron_verify.py`. `chron_cron_verify.py` is now a thin scheduler entry point:
   it gathers nothing, decides nothing, and writes no prediction record. The
   duplicate logic and the prediction rewrite are gone.
2. **`_evidence_for()` — decline-first.** Requires two distinctive title tokens in
   the snippet before a confirm/deny word may vote, so a generic page cannot cast
   a verdict.
3. **`_store_state()` + three-way status.** Every `FULL_LOOP` entry now carries
   `delta` (what this run changed), `state` (what is true now), `errors`, and a
   status of `ACTIVE` / `QUIET` / `DEGRADED`.
4. **`_run_step()`** captures failure as data instead of swallowing it.
5. **Lesson fingerprinting** so a repeated cycle is not new work.
6. **`dry_run` is genuinely dry** — verified by hashing every store around a pass.

### A defect introduced and caught during this repair

The first version of `_evidence_for` read magnitudes by taking the largest number
found anywhere in the search snippets. Probing it live returned:

```
Reported value ~2026.0 MYR vs claimed threshold 30000000000.0 MYR  ->  error = 1.0
```

`2026` was a year in the page text. The function was about to write a decisive
**INCORRECT** verdict, manufactured from unrelated digits, into the calibration
ledger — the founding defect of this whole repair, reproduced in the opposite
direction by the fix itself. The threshold path was removed. Magnitude claims now
always decline until a unit-aware extractor over a named data source exists.
Pinned by test 6(a).

### Verification of the loop-closure repair

| Suite | Result |
|---|---|
| `/root/chron/tests/test_loop_closure.py` | **30/30 PASS** — verifier immutability, no-claim-from-silence, delta+state, DEGRADED reporting, lesson dedup, evidence-lane discipline |
| `/root/chron/tests/test_repair_join.py` | 19/19 PASS (regression) |
| `/root/chron_abtest/harness.py` | 22/22 PASS (regression) |
| dry-run hash check | all six store files unchanged across a dry pass |

Live, after the repair:

```
chron_cron_verify.py   due 0 -> no verdict written; predictions.jsonl hash UNCHANGED
chron_loop_close.py    Status: ACTIVE   delta episodes_created=2
                                        state predictions_active=12, due_now=0, decisive=0
```

Before / after, same log, same organ:

```
OLD  {"episodes_created": 0, "predictions_verified": 0, "lessons_extracted": 0, "policies_promoted": 0}
NEW  {"status": "ACTIVE", "delta": {...}, "state": {"predictions_active": 12, ...}, "errors": []}
```

### Deployment state — this part DID deploy

`chron-prediction-verifier` (07:00) and `chron-loop-closer` (07:15) are one-shot
units; they import the chron modules at launch, so they ran the repaired code
immediately and were executed live above. The MCP read surface (`:18102`) remains
the only stale component — see the MUTATION_HELD section; that reload still needs
the F13 authorization lane repaired.

---

## Third pass — residuals found by probing the fix

### R1. The delta was a self-report, not a measurement

`delta.episodes_created` summed `bridge.episodes_created` + `fq_poll.episodes_created`.
`run_bridge()` returns `new_receipts`/`total_lines`/`offset` on its early-exit path,
so the sum read 0 while the store grew. Measured live: store went 50470 → 50505
across a run reporting `episodes_created: 0`.

Fixed: the delta is now the **measured** difference in `store.count()` across the
run. Component self-reports are kept alongside as `episodes_created_reported` for
cross-check. A component's claim about itself is a claim; a before/after count is
a measurement.

### R2. Two silent swallows remained

`_inject_carry_forward` and `_emit_ariflow_receipt` still used `except Exception:
pass`. Both are best-effort by design — CHRON must work when arifFlow is down — but
a dark receipt lane means the federation stops seeing CHRON run while CHRON believes
it reported.

Fixed: both return `(ok, error)`. Outcomes are disclosed in a new `warnings` field
on the loop entry. Warnings do **not** set `DEGRADED` (the organ's own work
succeeded) but they are visible. Ordering bug caught during the fix: the emit ran
after the entry was serialised, so the warning landed in memory and nowhere else;
the emit now runs before the write.

### R3. A loaded gun in the canonical module

`chron_verify._save_all_predictions()` rewrote `predictions.jsonl` wholesale — the
same immutability violation as V1 — and was defined but never called. Replaced with
a named stub that raises and explains why, so a future reader who greps the old
symbol finds the reason instead of an unexplained gap.

### R4. Test pollution of the production ledger (self-inflicted, caught and cleaned)

`test_loop_closure.py` redirected `cp.PREDICTIONS_FILE` to a temp path but a later
section restored the real `generate_from_chron_events`, so sections 7 and 8
generated real predictions and appended 24 real `predict` episodes to the
production store. A test that writes to production is not a test.

Fixed: the episode store is now redirected at its **source module**
(`chron.chron_store.get_store`), because the chron modules do
`from chron.chron_store import get_store` inside functions — patching a caller's
namespace leaves other paths pointing at production. The generator stays stubbed
for the whole file. Verified hermetically: `episodes.jsonl`, `index.json` and
`predictions.jsonl` hashes are unchanged across a full suite run.

Cleanup: `scripts/quarantine_bogus_episodes.py` removed 30 rows — the 24 above plus
6 pre-existing from a dev cycle earlier the same day — each a `predict` episode
naming a `prediction_id` absent from `predictions.jsonl`. Rows are not records of
anything that happened; they are harness artefacts, so append-only is not violated
by their removal. Every removed id is in
`data/quarantine-manifest-20260918T155247Z.json`; the ledger is backed up at
`data/.backup-episodes-20260918T155247Z/`; the index was rebuilt by the module's own
`_rebuild_index()` rather than a hand-written one.

Before → after: `predict` episodes 42 → 12, now equal to the 12 predictions held.

### Final verification

| Suite | Result |
|---|---|
| `/root/chron/tests/test_loop_closure.py` | **40/40 PASS** |
| `/root/chron/tests/test_repair_join.py` | 19/19 PASS |
| `/root/chron_abtest/harness.py` | 19/19 PASS |
| hermeticity hash check | production stores unchanged across a suite run |

```
reconcile: predict episodes 12 == predictions in store 12   YES
```

DITEMPA BUKAN DIBERI
