"""CHRON Frame — independent witness integration.

FRAME sits outside CHRON. FRAME:
  - Witnesses CHRON's outputs
  - Challenges contradictions
  - Provides independent reality grounding
  - Can say CHRON is wrong without permission

No self-certification. FRAME is the independent observer.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FRAME_URL = "http://127.0.0.1:18085"
CHRON_DATA = Path("/root/chron/data")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── FRAME WITNESS ─────────────────────────


def request_witness(
    claim: str,
    evidence: list[str],
    source: str = "chron",
) -> dict:
    """Request FRAME to witness a CHRON claim.

    FRAME returns:
      - witness_verdict: CONFIRMED | CONTESTED | UNVERIFIABLE
      - evidence_quality: OBSERVED | INFERRED | SPECULATIVE
      - contradictions: list of contradicting evidence
      - confidence: 0-1

    FRAME does NOT return SEAL/HOLD — that's arifOS's job.
    FRAME only witnesses reality.
    """
    # Store the witness request
    request = {
        "timestamp": _now_iso(),
        "claim": claim,
        "evidence": evidence,
        "source": source,
        "status": "PENDING",
    }

    request_file = CHRON_DATA / "frame_requests.jsonl"
    request_file.parent.mkdir(parents=True, exist_ok=True)
    with open(request_file, "a") as f:
        f.write(json.dumps(request, default=str) + "\n")

    return {
        "status": "PENDING",
        "message": "Witness request logged. FRAME processes asynchronously.",
        "request": request,
    }


def check_witness_results(claim_id: str | None = None) -> list[dict]:
    """Check FRAME witness results."""
    results_file = CHRON_DATA / "frame_results.jsonl"
    if not results_file.exists():
        return []

    results = []
    with open(results_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
                if claim_id and result.get("claim_id") != claim_id:
                    continue
                results.append(result)
            except json.JSONDecodeError:
                continue

    return results


def record_witness_result(
    claim: str,
    verdict: str,
    evidence_quality: str,
    contradictions: list[str],
    confidence: float,
) -> dict:
    """Record a FRAME witness result (called by FRAME, not by CHRON)."""
    result = {
        "result_id": f"frame-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "timestamp": _now_iso(),
        "claim": claim,
        "verdict": verdict,
        "evidence_quality": evidence_quality,
        "contradictions": contradictions,
        "confidence": confidence,
        "source": "frame",
    }

    results_file = CHRON_DATA / "frame_results.jsonl"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")

    return result


# ───────────────────────── CHALLENGE ─────────────────────────


def challenge_episode(episode: dict) -> dict:
    """Submit a CHRON episode for FRAME challenge.

    FRAME checks:
      1. Do the observations match reality?
      2. Are the claims supported by evidence?
      3. Are there contradictions?
      4. Is the confidence calibrated?
    """
    claims = episode.get("claims", [])
    observations = episode.get("observations", [])

    evidence = []
    for obs in observations:
        evidence.append(
            f"OBS: {obs.get('content', '?')} (source: {obs.get('source', '?')})"
        )
    for claim in claims:
        evidence.append(
            f"CLAIM: {claim.get('statement', '?')} (state: {claim.get('truth_state', '?')})"
        )

    return request_witness(
        claim=f"Episode {episode.get('episode_id', '?')} — {episode.get('function', '?')}",
        evidence=evidence,
        source="chron_challenge",
    )


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    import sys

    args = sys.argv[1:]

    if args and args[0] == "results":
        results = check_witness_results()
        print(f"FRAME witness results: {len(results)}")
        for r in results:
            print(
                f"  [{r.get('result_id', '?')}] {r.get('verdict', '?')} "
                f"confidence={r.get('confidence', '?')}"
            )
            if r.get("contradictions"):
                print(f"    contradictions: {r['contradictions']}")
    else:
        print("CHRON Frame — independent witness integration")
        print("  Use 'results' to check witness results")
        print(
            "  Use chron_mcp.call_tool('chron_episodes') to get episodes for challenge"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
