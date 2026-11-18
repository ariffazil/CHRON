# AGENTS.md — CHRON | Temporal Consequence & Calibration Organ

> **Authority Ceiling:** `555_COMPUTE_ONLY`
> **Port:** `18102` (`chron-mcp.service`) | **Public MCP:** `https://chron.arif-fazil.com/mcp`
> **Truth Rule:** Live `http://127.0.0.1:18102/health` and MCP `tools/list` beat static prose.

## Core Role
CHRON tracks dated, falsifiable predictions, closes verification windows against observed reality, scores empirical Brier calibration without inventing synthetic confidence priors, and reports attention debt (`AD`).

## Boundaries
- **Compute & witness only:** Never self-authorize mutations (`A-FORGE` acts), never issue `SEAL`/`HOLD`/`VOID` (`arifOS` judges), never own canonical VAULT999 memory.
- **Human-event privacy hygiene:** `chron_events.json` and `data/*.json*` MUST remain untracked and excluded from public pushes (`chron-push-mirror` enforces human-store filtering).
- **MCP surface (`24` tools):** Maintain parity across `server.py`, `mcp_annotations.py`, `llms.txt`, `claims.json`, and `README.md`.
- **Fail-closed contract:** Preserve `BL02` null-confidence semantics and causal disclosure rules (`chron_causal_contract.py`).

## Verification
```bash
uv lock --check
uv run pytest -q
```
