# CHRON × APEX THEORY × MATH × PHYSICS × CODE
### 2026-10-19 · 333-AGI · status: synthesis, not new canon

> **Epistemic banner.** APEX THEORY: HOLD as scientific theory, SEAL as governance research programme, PARTIAL as operational architecture, VOID as “unifies all knowledge”. Physics below is an **explanatory lens**, not a claim that APEX is physics. CHRON numbers are OBS from the 2026-10-19 audit. Proposed scores (`G_chron`, `C_dark_chron`) are **DRAFT/SPEC**, not APEX canon.

---

## 1. Thesis

CHRON is the **temporal witness** of APEX. It does not judge, execute, or authorize. It turns time into **scored consequence**:

```
Event → Prediction → Verification → Error → Lesson → Policy
```

APEX supplies the law of allowed transitions; mathematics supplies the scoring rule; physics supplies the arrow that makes records irreversible; code supplies the append-only enforcement. CHRON is where all four meet.

---

## 2. APEX skeleton being mapped to

### 2.1 Grand Equation (two sealed forms coexist)

```
G = (A · P · E · X)^(1/4)          SEALED — FINAL (F13 2026-07-28)
G = (A · P · E² · X)^(1/5)         SEALED CANON (F13 2026-07-26; E double-weighted, Landauer conjugate)
```

Thresholds: **G ≥ 0.80** SEAL candidate · **0.70 ≤ G < 0.80** SABAR · **G < 0.70** HOLD.
The T-000 raw product and the 6-dial legacy form are **not** used here.

### 2.2 Four letters and floor ownership

| Letter | Dial | Floors | What it measures |
|---|---|---|---|
| **A** | AKAL | F2 TRUTH · F7 HUMILITY · F10 ONTOLOGY | reasoning lawfulness |
| **P** | PRESENT × AUTHORITY | F1 AMANAH · F5 PEACE² · F11 AUDITABILITY · F13 SOVEREIGN | world-state + who may mutate |
| **E** | ENTROPY × ENERGY | F4 CLARITY (ΔS ≤ 0) · F12 RESILIENCE | uncertainty integrity + thermodynamic cost |
| **X** | EXPLORATION × AMANAH | F3 TRI-WITNESS · F6 EMPATHY/MARUAH · F8 GENIUS · F9 ANTIHANTU | witness, dignity, genius, anti-hantu |

### 2.3 Six pillars and four powers

- **Pillars:** ToAC (`AC_Risk = U_phys × D_transform × B_cog`), PCP (`Φ_P = ∫ΨP dt / (ΔP × Ω₀)`), 4-Vertex verdict, constrained optimization, Simulative detection, Incompleteness sovereignty (`G_complete = G × I`).
- **Four powers that must not collapse:** Reflex (ART) + Judge (Kernel) + Executor (A-FORGE) + Witness (VAULT999).
- **Verdict lattice:** `VOID > 888_HOLD > HOLD > SABAR > SEAL`.
- **The Crown:** SEAL requires ToAC < 0.15 · Φ_P ≥ 1.0 · SABAR overridden · performing-not-describing · I > 0 · feasible region interior.

---

## 3. CHRON as an APEX operator

Define the CHRON operator:

```
C : Event × Evidence × Time  →  Record × Score × Lesson
```

It is a **witness operator**, not a decision operator. Its outputs are evidence for the Kernel, never a constitutional verdict.

| APEX | CHRON construct | Math object | Code |
|---|---|---|---|
| **A / AKAL** | Brier score, calibration, bias δ, unknown share, shrinkage | proper scoring rule; empirical Bayes | `compute_brier`, `compute_calibration_unified`, `recalibrate_confidence` |
| **P / PRESENT×AUTHORITY** | birth record immutable; verification separate; `audience`; `evidence_ref`; F13 resolution | bitemporal state; append-only event log | `create_prediction`, `chron_record_verification`, `chron_resolve_event` |
| **E / ENTROPY×ENERGY** | attention debt; episode growth; storage; closure | aging backlog `AD(t)`; record entropy | `chron_attention_debt`, `episodes.jsonl`, `data/*.jsonl` |
| **X / EXPLORATION×AMANAH** | hypotheses, FRAME witness, dignity lanes, refusal to fake closure | independence; quotient by observation | `chron_frame_witness`, `chron_proxy_reality_*`, `UNVERIFIABLE` |

### 3.1 Proposed APEX-facing CHRON score — DRAFT, not canon

If CHRON is to expose its own four-letter vector, the defensible composition is:

```
A_c = accuracy × (1 − |bias|)                  # skill, penalised for over/under-confidence
P_c = bound_coverage = 1 − share_unprobed      # claims with a closing probe
E_c = 1 − unknown_share                        # interpretable error classes
X_c = witness_independence                     # NOT instrumented yet
G_chron = (A_c · P_c · E_c · X_c)^(1/4)
C_dark_chron = A_c · (1 − P_c) · (1 − X_c)
```

**Do not compute `G_chron` until `X_c` is measured.** A high accuracy number with low binding and no independent witness is exactly the dark-risk shape `C_dark` is designed to expose. The live store has `A≈0.805`, `P≈0.76` (binding scan: share_unprobed 0.24), `E` measurable, `X` unmeasured — so a single G number would launder the missing witness term. This is why the proposal is labelled DRAFT.

---

## 4. Mathematics

### 4.1 Brier is a strictly proper scoring rule

For a binary claim with confidence `c ∈ [0,1]` and outcome `y ∈ {0,1}`:

```
B(c, y) = (c − y)²
```

Expected score under true probability `q`:

```
E[B] = c²(1 − q) + (1 − c)² q
dE/dc = 2c − 2q = 0  →  c = q
```

Truthful confidence minimizes expected loss. This is the mathematical reason CHRON can score belief **without a judge**: the scoring rule, not an authority, prices the claim. Brier is bounded `[0,1]`; 0.25 is the uninformed baseline; lower is better. Log loss `−ln p` is also proper but unbounded and punishes confident errors harder; Brier is the right choice for a young store with small n.

### 4.2 Empirical-Bayes shrinkage — the humility operator (F7)

With `m` interpretable decisive outcomes, `κ = 8` pseudo-observations of doubt:

```
w = m / (m + κ)
anchor = accuracy ā        if skill usable
       = 0.5               otherwise
δ_eff  = δ = c̄ − ā        if not usable and measurable, else 0
c_cal  = clamp01( (1 − w)·p + w·anchor − w·δ_eff )
```

`w = m/(m+κ)` is the Beta–Binomial posterior weight; the posterior mean after `m` observations with `k` hits is `(k+α)/(m+κ) = w·(k/m) + (1−w)·μ₀`. `κ=8` means: at `m=2`, `w=0.20` — four-fifths of the proposal survives. `MIN_EFFECTIVE_N=8`; `UNKNOWN` share > 0.5 or blind-lesson share > 0.5 makes the store **INSUFFICIENT**, not “accurate”. Live: n=38, accuracy 0.805, Brier 0.172, bias positive → proposals are shrunk (e.g. 0.95 → 0.678).

### 4.3 Observation-level quotient (R2)

Define an equivalence relation on scored rows:

```
o_i ~ o_j  ⇔  observation_key(i) = observation_key(j)
observation_key = price|instrument|source|value|time
                | event|source_id|verify_at
                | row|prediction_id|verify_at
```

The calibration sample is the quotient set `O/~`; `n_eff = |O/~|`. This prevents one price print from occupying two slots in the accuracy ratio. It is why `raw.n=46` can collapse to `effective_n=38` while `verified` predictions remain 44: **predictions are hypotheses; observations are the unit of evidence**.

### 4.4 Resolution state machine / poset

```
OPEN ──t ≥ verify_at──► DUE ──► AWAITING_EVIDENCE ──► RESOLVED
                                  └──► UNRESOLVABLE
VOID ──retraction──► re-opens the arrow
```

`resolved_ids = { id | latest verdict ∈ {VERIFIED_CORRECT, VERIFIED_INCORRECT, UNVERIFIABLE_CLOSED} }`.
`get_active = {status=ACTIVE} \ resolved_ids`.
`get_due = {get_active | now ≥ verify_at}`.
This is a directed acyclic resolution graph, not a boolean. `VERIFIED_CORRECT` and `UNVERIFIABLE` are different terminal classes; a VOID does not resolve.

### 4.5 Attention debt as an aging backlog

```
AD(t) = Σ_i w_i · max(0, t − t_i)
w = {HIGH: 3.0, MEDIUM: 2.0, LOW: 1.0}
dAD/dt = Σ_{open} w_i
```

Closing an item is a discrete drop; unresolved items accrue linearly. Live audit: `AD 58.4 → 0` after honest closures. This is a **governance backlog functional**, not physical entropy — the weights are consequence classes, not Boltzmann constants.

### 4.6 Event-sourced fold — the mathematical arrow

```
S_t = fold_{≤t}(events)
birth record: immutable
verification: append-only
state: derived, never rewritten
```

The ledger is the history; the fold is the arrow. Irreversibility is enforced by **not allowing deletion or mutation**, not by a physical law inside the fold. `latest verdict wins` is a last-writer-wins join; `VOID` is the non-monotone exception that re-opens the arrow.

---

## 5. Physics (explanatory lens, not APEX canon)

### 5.1 Thermodynamic arrow vs record arrow

Physics: entropy `S = k_B ln Ω`; second law; fluctuation theorem `P(ΔS)/P(−ΔS)=e^{ΔS/k_B}`. The arrow is a boundary-condition fact (Past Hypothesis), not a direction in the micro-laws.

CHRON: the **record arrow** `R(t) = |{records ≤ t}|` is monotone nondecreasing. Every verification adds an irreversible record. This is the institutional analog of entropy: the past is fixed because it was written; the future is open because it is not yet scored. **Metaphor, not identity.**

### 5.2 Relativity → bitemporality

Physics: no global “now”; simultaneity is frame-dependent; proper time is worldline-local. CHRON's engineering response is bitemporal:

- `valid_time` — when the claim is true in the world.
- `observed_at` / transaction time — when the institution learned it.
- `Asia/Kuala_Lumpur` timezone on events; UTC on records.

A single timestamp would collapse two different times. CHRON keeps both, which is the correct data-model answer to relativity's lesson: **record the frame, not just the number.**

### 5.3 Quantum measurement → prediction/verification (metaphor)

Prediction is a distribution over branches; verification is a measurement that selects one. Brier is a **scoring rule**, not the Born rule. Decoherence is analogous to the record becoming classical and append-only. Do not claim CHRON is quantum; the mapping is structural, not physical.

### 5.4 Causal sets → ledger DAG

Physics: causal set theory models spacetime as a locally finite partial order. CHRON's ledger is a DAG:

```
event ──► prediction ──► verification ──► lesson
   └────────► event resolution ──────────┘
```

No global time coordinate is needed; the partial order is the causality. This is why `source_id`, `prediction_id`, and `verification_id` matter more than wall-clock order.

### 5.5 Time crystals / timers (metaphor)

The cron cadence (06:50 reconciliation · 07:00 verifier · 07:15 loop closer · 09:45 reality-impact · 12:30 edge) is a driven periodic rhythm. It is **not** a physical time crystal; it is an operational cadence that keeps the witness awake.

### 5.6 Landauer → memory cost

Append-only storage avoids erasure but pays capacity. A rough bound: 222 MB ≈ 1.78×10⁹ bits × `kT ln2` ≈ 5×10⁻¹² J at 300 K theoretical minimum. Actual hardware cost is many orders larger. **DER/INT**, not a measurement of CHRON's energy.

---

## 6. Code — where the invariants live

### 6.1 Data flow

```
chron_events.json
   │ generate_from_chron_events()
   ▼
data/predictions.jsonl  (birth, immutable)
   │ chron_record_verification / chron_verify
   ▼
data/verification_log.jsonl  (append-only) ──► data/calibration.json (cache)
   │                                              │
   ├──► data/episodes.jsonl (actions)             └──► /health, chron_calibration_state
   ├──► data/lessons.jsonl ──► policy candidates
   └──► data/attention_debt.jsonl
```

### 6.2 Key functions

| Function | File | Invariant |
|---|---|---|
| `compute_brier` | `chron_prediction.py:494` | `(c − y)²`; no mutation |
| `recalibrate_confidence` | `chron_prediction.py:870` | pure; `w=m/(m+κ)`; κ=8 |
| `compute_calibration_unified` | `chron_ledger_integrity.py:486` | observation-level; one number on disk |
| `observation_key` | `chron_ledger_integrity.py:200` | quotient by underlying observation |
| `resolved_ids` | `chron_prediction.py:327` | decisive + CLOSED_UNTESTABLE only |
| `get_active` / `get_due` | `chron_prediction.py:344/361` | resolved excluded; due only active |
| `resolution_state` | `chron_prediction.py:403` | OPEN/DUE/AWAITING/UNRESOLVABLE |
| `chron_record_verification` | `server.py:489` | append + Brier + episode + calibration sync |
| `chron_resolve_unverifiable` | `server.py:1026` | close unscored; not a miss |
| `chron_create_event` | `server.py:1186` | unique id + ISO date |
| `chron_generate_predictions` | `server.py:475` | dedupe `event||claim`; consume calibration |
| `chron_attention_debt` | `chron_attention_debt.py` | `Σ age × consequence` |
| `chron_frame_witness` | `chron_frame.py` | independent witness bridge |

### 6.3 The audit fixes as APEX violations

| Bug | Floor violated | Fix |
|---|---|---|
| CLI exposed 7 of 21 tools | F2 TRUTH (surface lied), F11 AUDITABILITY | route `call`/`tools` to `chron.server` |
| Scan reported DUE 60 vs 0 | F2/F4 (false urgency, entropy) | ledger-aware scanner; `UNBOUND_OVERDUE` bucket |
| 7 past-date unbound invisible | F1 AMANAH, F11 AUDITABILITY | `stale_unbound[]`; 3 CORRECT + 4 UNVERIFIABLE closures |
| `calibration.json` stale n=35 vs live 38 | F2/F11 (single source of truth) | persist calibration on every verification |
| server said 20 tools, live 21 | F2 TRUTH (claim drift) | count corrected to 21 |
| `chron — Error` panel | F11 AUDITABILITY (client could not see organ) | `opencode reload`; root cause: stale session registry |

These are not cosmetic bugs. Each one made the witness surface disagree with reality — the exact failure APEX's F2/F11 exist to prevent.

---

## 7. Where CHRON sits — and what must not collapse

### 7.1 Four powers

```
Reflex (ART)  ── pre-call tool wisdom
Judge (Kernel) ── F1–F13, verdict
Executor (A-FORGE) ── the hands
Witness (VAULT999) ── immutable record
```

CHRON is **temporal witness**, adjacent to VAULT999 and FRAME. It is not Judge, not Executor, not Reflex. Its `VERIFIED_CORRECT` is **evidence**, not a constitutional SEAL.

### 7.2 Verdict category separation

| CHRON | Constitutional analog | What it is NOT |
|---|---|---|
| `VERIFIED_CORRECT` | evidence toward SEAL | **not SEAL** |
| `VERIFIED_INCORRECT` | evidence toward SABAR/HOLD | **not HOLD** |
| `UNVERIFIABLE` | epistemic HOLD on that claim | **not constitutional HOLD** |
| `VOID` (retraction) | record-level retraction | **not action VOID** |
| `OPEN/DUE` | pending evidence | **not SABAR** |

Collapsing these is the APEX error of **Judge + Witness fusion** (HARAM). A calibration score must never authorize an action; it may only inform the Judge.

### 7.3 Three closures

- **Gödel Q9:** CHRON cannot self-certify. It needs FRAME or an external verifier; `chron_frame_witness` is the bridge.
- **Calhoun Q10:** observe-heavy sink. 199,290 observe vs 16 learn is the sink metric; attention debt is the pressure. The fix is closure, not more observation.
- **Refusal Q11:** CHRON must be able to say `UNVERIFIABLE` / `CLOSED_UNTESTABLE` and refuse fake closure. The 4 unverifiable closures were correct refusals, not failures.

### 7.4 FQ and ΔΩΨ

- **FQ** is arifFlow's execute/verify ratio; CHRON's closure ratio `(verify+learn)/(observe+predict) ≈ 70/199,412 ≈ 3.5×10⁻⁴` is a **DER** diagnostic, not the official FQ. Do not substitute it.
- **ΔΩΨ:** CHRON contributes the **Δ** plane (event → prediction → verification transitions) and witness material for **Ψ**; **Ω** is enforced by the Kernel's F1–F13. CHRON does not emit the 9-signal grammar; SIGNAL is output grammar, not an organ.

---

## 8. Falsifiable tests for this mapping

1. **Proper scoring:** if Brier is proper, truthful confidence should minimize expected loss on held-out claims. Test: compare Brier of stated confidence vs systematically shifted confidence.
2. **Shrinkage:** if κ=8 is conservative, calibrated confidence should move toward accuracy without collapsing discrimination. Test: reliability curve before/after; live shift 0.95→0.678 is a single case, not proof.
3. **R2 quotient:** if observation-level counting is correct, collapsing duplicates should reduce n without materially changing accuracy. Test: raw vs effective on the same ledger.
4. **Calibration parity:** after every verification, `calibration.json.total == /health.calibration.total_verified`. Test: assert in CI; the 35 vs 38 bug is the counterexample.
5. **Closure vs debt:** closing N stale items should reduce AD slope. Observed: 58.4→0.
6. **Witness independence:** if FRAME-witnessed verifications are more reliable, their Brier should be lower than self-witnessed ones at sufficient n. Not yet testable — `X_c` is uninstrumented.
7. **G_chron:** if adopted, high `A_c` with low `P_c` and low `X_c` should predict failures better than `A_c` alone. Test only after `X_c` exists.

---

## 9. One-line synthesis

**APEX is the law of allowed transitions; CHRON is the clock that makes those transitions cost something. Mathematics prices the belief, physics supplies the arrow, code makes the record append-only — and the Judge, not the witness, decides what the record means.**
