# R1 SPEC — Bring Sibling Ledgers Under the Fence
Status: PROPOSAL · NOT IMPLEMENTED · F13 ratification required before code
Author: Claude (auditor→reducer)  Date: 2026-10-22  Repo: CHRON + A-FORGE
Blast class: verdict-field semantics change — same class as `f13-t6-verdict-semantics`

## 0. One-line statement
CHRON accepts verification writes that change calibration without an identity,
without a witness, and without a retraction path that actually retracts. R1 closes
that: every write to a verdict-bearing ledger must pass through one fence that
binds identity, witness, and an *effective* inverse.

## 1. Observed reality (evidence before repair)
Measured 2026-10-22. Production ledger untouched by this proposal.

| Fact | Evidence |
|---|---|
| Write accepted with no identity binding | `verif-9326cd2bfb1f` — `binding=HUMAN_WITNESS`, `witness_status=UNWITNESSED`, `calibration_synced=true` |
| VOID does NOT erase the row it retracts | `pred-fdb40342252b = CORRECT + VOID + INCORRECT` in production; sandbox replay `/tmp/chron-contra-btv86xw1` |
| No agent retraction path | `chron_resolve_unverifiable` → `ALREADY_VERIFIED` |
| Settlement is last-write-wins | `chron_ledger_integrity.py:158` "Last-write-wins is deliberate" |
| Reopen rule | `chron_verify.py:582 _is_already_verified` — "latest row is decisive" reopens |

Invariant violated (already ratified):
```
Authority ≠ Execution
Witness before mutation
No system may become its own authority
```
Consequence precedes Witness.

## 2. Which verdict field changes meaning
| Field | Old meaning (declared) | New meaning (R1) |
|---|---|---|
| `VOID` | "a row that retracts a prior verification" | "a row that *terminates* the prediction's decidability; the prior decisive row is removed from the settled set" |
| `witness_status` | free-text label | **required** enum; `UNWITNESSED` ⇒ write is `PROVISIONAL`, excluded from calibration |
| `calibration_synced` | set true on any write | set true **only** when the settled row is `WITNESSED` and decisive |
| `resolver_relation` | informational | must be a *different* principal than the original verifier for a retraction to bind |

## 3. Old vs new writers/readers
| Role | Old | New |
|---|---|---|
| Writer | `chron_record_verification` writes on demand, no identity gate | must carry an authenticated identity; `same_agent` resolver ⇒ `PROVISIONAL` |
| Retractor | `chron_resolve_unverifiable` — refused if already verified | a dedicated retraction path; `VOID` is terminal; prior decisive row leaves the settled set |
| Reader (calibration) | `compute_calibration_unified` counts settled rows incl. pre-VOID | counts only WITNESSED+decisive rows; VOID is a tombstone, not a skip |
| Reader (attention debt) | reads verdict count | reads *effective* verdict count (post-tombstone) |

## 4. Falsifiable tests (must FAIL before the fix, PASS after)
Each test is a live probe against a sandbox copy; production stays untouched.

**T1 — VOID is terminal**
```
setup:   write CORRECT for pred-X
act:     write VOID for pred-X
then:    write INCORRECT for pred-X
assert:  INCORRECT is REFUSED (pred-X is terminally retracted)
```
Current: ACCEPTED (see `pred-fdb40342252b`). After R1: refused.

**T2 — Unwitnessed write cannot move calibration**
```
setup:   baseline Brier for the corpus
act:     write a PROVISIONAL (UNWITNESSED) verification
assert:  Brier is UNCHANGED
```
Current: Brier moves (`calibration_synced=true`). After R1: unchanged.

**T3 — Same-principal retraction does not bind**
```
setup:   verifier V1 writes CORRECT
act:     same principal V1 writes VOID (resolver_relation=same_agent)
assert:  retraction is PROVISIONAL, does not remove V1's decisive row
```
Current: retraction removes it. After R1: does not.

**T4 — No identity ⇒ no verdict**
```
act:     write verification with no authenticated identity
assert:  write is rejected or PROVISIONAL; `calibration_synced` stays false
```

## 5. Rollback
- **Code:** single commit; revert with `git revert <sha>` in CHRON. No schema migration
  is proposed in this doc.
- **Data:** R1 must NOT rewrite history. Existing rows keep their bytes; the change is in
  *how settlement reads them*. If calibration changes on deploy, the previous calibration
  artifact is retained under a dated name (`chron_calibration.<ts>.json`) so a recompute
  from old semantics is possible.
- **Kill switch:** settlement reader takes a `SETTLEMENT_SEMANTICS=legacy|fenced` flag;
  `legacy` restores pre-R1 counting without a code revert. Default flips only after F13.
- **Reversibility class:** EXECUTE_REVERSIBLE (flag + artifact retention).

## 6. What R1 does NOT touch
- No change to the write PATH for non-verdict ledgers.
- No change to arifOS kernel authority (A-FORGE does not adjudicate floors).
- No change to how the chain is hashed or sealed.
- Push and deploy remain F13 — this document does not execute either.

## 7. Why this is the smallest highest-leverage change
R1 is the only repair that closes the P0 class: a mutation with consequences while
witness and authority are misaligned. Observability repairs (R2/R3/R5) make the defect
*visible*; R1 makes it *impossible*. A system that can report its own health but cannot
prevent an unwitnessed consequence is still unsafe.
