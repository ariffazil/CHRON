# Security Policy

## Supported Versions

The arifOS Federation operates on a rolling release model. The latest commit on
`main` is the supported version for `ariffazil/CHRON`.

| Version | Supported |
|---|---|
| `main` | Yes |
| Other | No |

## Reporting a Vulnerability

**Please do NOT open a public issue for security vulnerabilities.**

1. Contact the maintainers directly at **arifbfazil@gmail.com**.
2. Include steps to reproduce, affected files/endpoints, and potential impact.
3. We acknowledge receipt within 48 hours and coordinate remediation.

## Security & Privacy Boundaries

- **Data-free public repository:** Private runtime ledgers (`data/*.jsonl`, `data/*.json`) and human/calendar event stores (`chron_events.json`) are excluded by `.gitignore` and never committed to public `main`.
- **Authority ceiling (`555_COMPUTE_ONLY`):** CHRON computes and records predictions, verifications, calibration metrics, and attention debt. It never executes infrastructure mutations (`A-FORGE`) or issues constitutional verdicts (`arifOS`).
- **Fail-closed calibration:** Absent confidence/evidence stays `null` / `UNVERIFIABLE`; never backfilled with synthetic defaults.
- **No runtime credentials required:** `chron-mcp.service` runs isolated without LLM provider keys; public ingress is terminated cleanly behind Caddy (`https://chron.arif-fazil.com`).
