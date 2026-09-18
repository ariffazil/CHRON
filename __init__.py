"""CHRON — Temporal Consequence Tracker.

The organ that answers:
  - What did we think?
  - What happened?
  - Were we wrong?
  - What changed because of it?

NOT a scheduler. NOT a memory store. NOT a briefing engine.
The temporal cortex of the arifOS federation.

Modules:
  chron_store          — Append-only bitemporal episode ledger
  chron_episode        — Episode factory (observe/predict/verify/learn)
  chron_prediction     — Prediction lifecycle + Brier calibration
  chron_verify         — Verification engine (checks predictions at verify_at)
  chron_learn          — Lesson extraction from verified predictions
  chron_frame          — Independent witness integration (FRAME)
  chron_ariflow_bridge — Reads arifFlow receipts + FQ daemon poll
  chron_mcp            — Query API for other organs (7 tools)
  chron_temporal_root  — TEMPORAL_ROOT schema (kernel-level temporal validity)
  chron_e2e_proof      — End-to-end event loop proof
  chron_loop_close     — Wires the three UNBUILT arrows (OUTCOME→LEARNING, EXPERIENCE→MEMORY, MEMORY→POLICY)

Temporal Contract: /root/arifOS/docs/TEMPORAL-CONTRACT-2026-09-18.md
APEX Theory: /root/arifOS/docs/APEX-THEORY-RELATIVITY-CONTRAST-QUANTUM-MEANING-2026-09-18.md

DITEMPA BUKAN DIBERI ⚒️
"""

__version__ = "0.2.0"
