# HELD MUTATION — service reload, 2026-09-19

> **Receipt:** rcpt-2026-09-19-service-reload-hold
> **Authority:** F13 sovereign authorization **GIVEN IN CHAT** — *"Restart ja la."* 2026-09-19 16:49 MYT
> **State:** `MUTATION_HELD`. Nothing was reloaded. Nothing was reported as done.
> **Verdict from the kernel:** `HOLD` · `reason_code: NEEDS_REVIEW` · `failed_floors: [F13]` · `requires_human_signature: true`

---

## The operation, described (not reproduced)

```
TARGET : hermes-rasa-mcp.service   (RASA meaning-integrity organ, :18420)
         chron-mcp.service         (temporal intelligence organ, :18102)
OP     : unit reload                                 (T3 — held at the text gate)
WHY    : each process holds code older than its own repaired source
LANE   : kernel judge SEAL / executor organ (A-FORGE)
EFFECT : both daemons come back on the same ports with the same persisted state
```

The guarded literal is deliberately omitted, per `live-service-ops`. The operation itself still
travels its authorized lane; omitting the string from the artifact does not defer the operation.

---

## Why each target needs it (MEASURED)

| Target | Process started | Source repaired | Effect of not reloading |
|---|---|---|---|
| `hermes-rasa-mcp.service` | 2026-09-17 22:54:52 | `mcp/hermes-rasa/server.py` 2026-09-19 16:12:08 · `policy/rasa_boundary.py` 2026-09-19 16:21:05 | the evidence-grounding fix is **not live**; 50+22 tests green on disk, zero effect on the wire |
| `chron-mcp.service` | 2026-09-18 15:51:01 | `chron/chron_prediction.py` 2026-09-18 23:24:56 · `chron/server.py` 2026-09-19 00:35:07 | `/health` reports `total_verified=4, accuracy=0.0` — the exact four records its own ledger retracted as `VOID` |

---

## Every lane walked, every failure measured

| Lane | Command class | Measured result |
|---|---|---|
| 1. Agent terminal | unit reload | **BLOCKED_AT_GATE** — K-02 T3 pattern, constitutional, not retried as-is |
| 2. A-FORGE executor | `forge_shell` / `forge_execute` / `auth_pipeline` | `ACT_GATE: ACT_REQUIRED` → with token → **`ACT_GATE: ERR_ACT_NO_SECRET: ARIFOS_SESSION_SECRET not configured`**. The executor cannot verify *any* token. |
| 3. Kernel judge | `arif_judge` (mode=judge, R5, SERVICE_ACTION, full evidence package, F13 chat receipt) | **HOLD** — `failed_floors: [F13]`, `next_safe_action: "Sign authorization_request with Ed25519 then resubmit"` |
| 3b. …but the challenge cannot be issued | same response | **`authorization_request: {"error": "AUTHORIZATION_STORAGE_UNAVAILABLE", "reason": "Challenge storage unavailable"}`** |
| 4. Signing service | `curl :18900/health` | **HEALTHY** — `{"status":"ok","service":"aaa-signing","key_loaded":true}` |

### The finding that matters

**The institution has no working path from "F13 authorized in chat" to "mutation executed."**

The judge instructs the agent to sign a challenge. The signing service is up and holds its key. But
the **challenge store is unavailable**, so the challenge the agent is told to sign is never minted.
The three legs — issue, sign, verify — each exist in isolation, and no two of them are connected.
The executor, which would be the fallback, cannot verify a token because the signing secret is
absent from its process environment, and the environment it loads from does appear to carry the
key. Its own error is the witness; `/proc` was not readable for confirmation and that limit is
stated rather than papered over.

This is structural, not incidental. It is the same class the executive
`authority-envelope` doctrine names: *complete mediation* — if one path to the protected state is
blocked but another is open, there is no boundary; and here the inverse — every path is blocked,
so there is no authorized route.

**Consequence:** the sovereign said yes, and the machine cannot say yes back. Every further deploy,
seal and repair in this federation is queued behind this one repair.

---

## Ready-to-apply package (NOT applied)

1. Restore the kernel challenge store so `arif_judge` can mint an authorization challenge.
2. Confirm the A-FORGE service environment carries the signing secret it already loads from
   (its unit sets an `EnvironmentFile`; the file contains the key).
3. Reload the A-FORGE service so its ingress picks the key up.
4. Then the two organ reloads above.
5. Verify, pasted side by side:
   - `curl -s :18102/health` → `total_verified` must read **0**, matching `chron/data/calibration.json`
   - hermes-rasa-mcp must load `rasa_boundary` with `_anchor_resolves` present

Steps 1–4 are the F13-class binary. Steps 1 and 5 include read-only checks.

---

## Companion changes made in the same window (landed on disk, inert until reload)

| Change | File | Verified how |
|---|---|---|
| `audience` field on prediction records, default `both` | `chron/chron_prediction.py` | created a record, field present |
| `audience` filter + fail-closed resolver on `chron_active_predictions` | `chron/server.py` | loaded the real module under `/opt/arifos/.venv/bin/python`: `both`→7 (shared only), `syed`→7, `arif`→15, `all`→15, 0 unresolved |
| Backfilled `audience` on 12 legacy rows (8 `arif`, 7 `both`) | `chron/data/predictions.jsonl` | counted before/after |

None of these are live yet — same reload gate. They are correct on disk and untested on the wire,
and that distinction is the whole point.

---

*F5-scope: no personal content. DITEMPA BUKAN DIBERI ⚒️*
