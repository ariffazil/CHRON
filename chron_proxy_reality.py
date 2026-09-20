"""CHRON Proxy-Reality Correlation — tracks whether proxy improvements
correlate with reality improvements.

Implements Gap 2 of the Proxy-Reality Paradox doctrine:
  "CHRON adds a proxy-reality correlation metric — track whether improving
   a proxy correlates with improving the reality it represents."

Each organ declares a proxy→reality pair in its init-to-seal prompt.
This module tracks observations over time and computes whether proxy
trends match reality trends — or diverge.

Divergence states:
  HEALTHY     — proxy ↑, reality ↑  (or both stable)
  GAMING      — proxy ↑, reality ↓  (proxy being optimized, reality ignored)
  WRONG_PROXY — proxy ↓, reality ↑  (wrong metric being tracked)
  FAILING     — proxy ↓, reality ↓  (systemic degradation)
  UNKNOWN     — insufficient data

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

DATA_DIR = Path("/root/chron/data")
PAIRS_FILE = DATA_DIR / "proxy_reality_pairs.json"
OBSERVATIONS_FILE = DATA_DIR / "proxy_reality_observations.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ───────────────────────── PAIR REGISTRY ─────────────────────────


def _load_pairs() -> dict[str, dict]:
    if not PAIRS_FILE.exists():
        return {}
    try:
        return json.loads(PAIRS_FILE.read_text())
    except Exception:
        return {}


def _save_pairs(pairs: dict[str, dict]):
    _ensure_dir()
    PAIRS_FILE.write_text(json.dumps(pairs, indent=2, default=str))


def register_pair(
    organ: str,
    proxy_name: str,
    proxy_description: str,
    reality_name: str,
    reality_description: str,
    divergence_threshold: float = 0.3,
    diagnostic_question: str = "",
) -> dict:
    """Register a proxy→reality pair for an organ.

    Args:
        organ: organ name (e.g., "GEOX", "WEALTH", "333-AGI")
        proxy_name: short name for the proxy metric
        proxy_description: what the proxy measures
        reality_name: short name for the reality metric
        reality_description: what reality it should represent
        divergence_threshold: min delta between proxy and reality trends
            to flag divergence (0.0-1.0)
        diagnostic_question: the "every X must ask" question from init-to-seal

    Returns:
        The registered pair record.
    """
    pairs = _load_pairs()
    pair_id = f"pr-{organ.lower()}-{uuid.uuid4().hex[:8]}"

    pair = {
        "pair_id": pair_id,
        "organ": organ,
        "proxy_name": proxy_name,
        "proxy_description": proxy_description,
        "reality_name": reality_name,
        "reality_description": reality_description,
        "divergence_threshold": divergence_threshold,
        "diagnostic_question": diagnostic_question,
        "status": "ACTIVE",
        "registered_at": _now_iso(),
        "last_observed_proxy_at": None,
        "last_observed_reality_at": None,
        "last_correlation_state": "UNKNOWN",
    }

    pairs[pair_id] = pair
    _save_pairs(pairs)
    return pair


def list_pairs(organ: Optional[str] = None) -> list[dict]:
    """List registered pairs, optionally filtered by organ."""
    pairs = _load_pairs()
    result = list(pairs.values())
    if organ:
        result = [p for p in result if p["organ"].upper() == organ.upper()]
    return result


# ───────────────────────── OBSERVATIONS ─────────────────────────


def _load_observations(pair_id: Optional[str] = None) -> list[dict]:
    """Load observations, optionally filtered by pair_id."""
    if not OBSERVATIONS_FILE.exists():
        return []
    obs = []
    with open(OBSERVATIONS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if pair_id is None or rec.get("pair_id") == pair_id:
                    obs.append(rec)
            except json.JSONDecodeError:
                continue
    return obs


def observe(
    pair_id: str,
    metric_type: str,
    value: float,
    unit: str = "",
    source: str = "",
    evidence_ref: str = "",
) -> dict:
    """Record an observation for a proxy or reality metric.

    Args:
        pair_id: the proxy-reality pair ID
        metric_type: "proxy" or "reality"
        value: the observed value (normalized to 0.0-1.0 where possible)
        unit: unit of measurement (for display only)
        source: what tool/system produced this value
        evidence_ref: path or URL to supporting evidence

    Returns:
        The observation record.
    """
    if metric_type not in ("proxy", "reality"):
        raise ValueError("metric_type must be 'proxy' or 'reality'")

    pairs = _load_pairs()
    if pair_id not in pairs:
        raise ValueError(f"Unknown pair_id: {pair_id}")

    obs = {
        "observation_id": f"obs-{uuid.uuid4().hex[:12]}",
        "pair_id": pair_id,
        "metric_type": metric_type,
        "value": float(value),
        "unit": unit,
        "source": source,
        "evidence_ref": evidence_ref,
        "observed_at": _now_iso(),
    }

    _ensure_dir()
    with open(OBSERVATIONS_FILE, "a") as f:
        f.write(json.dumps(obs, default=str) + "\n")

    # Update pair's last-observed timestamps
    pairs[pair_id][f"last_observed_{metric_type}_at"] = obs["observed_at"]
    _save_pairs(pairs)

    return obs


# ───────────────────────── CORRELATION ─────────────────────────


def _compute_trend(values: list[float], window: int = 5) -> str:
    """Compute trend direction from the last N values.

    Returns: "up", "down", "flat", or "insufficient"

    Simple linear slope sign over the window.
    """
    recent = values[-window:]
    if len(recent) < 2:
        return "insufficient"

    # Simple slope: compare mean of first half to mean of second half
    mid = len(recent) // 2
    first_half = sum(recent[:mid]) / max(len(recent[:mid]), 1)
    second_half = sum(recent[mid:]) / max(len(recent[mid:]), 1)
    delta = second_half - first_half

    threshold = 0.05  # minimum change to count as a trend
    if delta > threshold:
        return "up"
    elif delta < -threshold:
        return "down"
    return "flat"


def _trend_to_delta(trend: str) -> int:
    """Convert trend to numeric for correlation comparison."""
    return {"up": 1, "down": -1, "flat": 0, "insufficient": 0}[trend]


def compute_correlation(
    pair_id: str,
    window: int = 5,
) -> dict:
    """Compute proxy-reality correlation for a single pair.

    Returns:
        Correlation state including divergence classification.
    """
    pairs = _load_pairs()
    pair = pairs.get(pair_id)
    if not pair:
        return {"error": f"Unknown pair_id: {pair_id}"}

    obs = _load_observations(pair_id)
    proxy_values = [o["value"] for o in obs if o["metric_type"] == "proxy"]
    reality_values = [o["value"] for o in obs if o["metric_type"] == "reality"]

    proxy_trend = _compute_trend(proxy_values, window)
    reality_trend = _compute_trend(reality_values, window)

    proxy_delta = _trend_to_delta(proxy_trend)
    reality_delta = _trend_to_delta(reality_trend)

    # Classify divergence state
    if proxy_trend == "insufficient" or reality_trend == "insufficient":
        state = "UNKNOWN"
    elif proxy_delta == reality_delta:
        # Same direction = healthy (both up, both down, both flat)
        state = "HEALTHY" if proxy_delta >= 0 else "FAILING"
    elif proxy_delta > 0 and reality_delta < 0:
        state = "GAMING"
    elif proxy_delta < 0 and reality_delta > 0:
        state = "WRONG_PROXY"
    elif proxy_delta > 0 and reality_delta == 0:
        state = "HEALTHY"  # proxy improving, reality stable = acceptable
    elif proxy_delta == 0 and reality_delta < 0:
        state = "FAILING"  # stable proxy but degrading reality
    else:
        state = "HEALTHY"

    result = {
        "pair_id": pair_id,
        "organ": pair["organ"],
        "proxy_name": pair["proxy_name"],
        "reality_name": pair["reality_name"],
        "diagnostic_question": pair.get("diagnostic_question", ""),
        "proxy_trend": proxy_trend,
        "reality_trend": reality_trend,
        "proxy_observations": len(proxy_values),
        "reality_observations": len(reality_values),
        "proxy_latest": proxy_values[-1] if proxy_values else None,
        "reality_latest": reality_values[-1] if reality_values else None,
        "proxy_window": proxy_values[-window:] if proxy_values else [],
        "reality_window": reality_values[-window:] if reality_values else [],
        "correlation_state": state,
        "is_divergent": state in ("GAMING", "WRONG_PROXY"),
        "diagnostic_needed": state in ("GAMING", "WRONG_PROXY", "FAILING"),
        "computed_at": _now_iso(),
    }

    # Update pair's cached state
    pairs[pair_id]["last_correlation_state"] = state
    _save_pairs(pairs)

    return result


def compute_all(organ: Optional[str] = None, window: int = 5) -> dict:
    """Compute correlation for all registered pairs.

    Returns:
        Summary with per-pair details and overall health.
    """
    pairs = list_pairs(organ)
    results = []
    for pair in pairs:
        result = compute_correlation(pair["pair_id"], window)
        results.append(result)

    states = [r["correlation_state"] for r in results if "error" not in r]
    divergent = [r for r in results if r.get("is_divergent")]
    needs_diagnostic = [r for r in results if r.get("diagnostic_needed")]

    return {
        "pairs": results,
        "total": len(results),
        "healthy": sum(1 for s in states if s == "HEALTHY"),
        "gaming": sum(1 for s in states if s == "GAMING"),
        "wrong_proxy": sum(1 for s in states if s == "WRONG_PROXY"),
        "failing": sum(1 for s in states if s == "FAILING"),
        "unknown": sum(1 for s in states if s == "UNKNOWN"),
        "divergent_count": len(divergent),
        "needs_diagnostic_count": len(needs_diagnostic),
        "divergent_pairs": [
            {
                "organ": r["organ"],
                "proxy": r["proxy_name"],
                "reality": r["reality_name"],
                "state": r["correlation_state"],
                "question": r["diagnostic_question"],
            }
            for r in divergent
        ],
        "computed_at": _now_iso(),
    }


# ───────────────────────── SEED DEFAULTS ─────────────────────────

# Pre-registered pairs from init-to-seal proxy→reality blocks.
# These are registered on first import if no pairs exist yet.

DEFAULT_PAIRS = [
    {
        "organ": "333-AGI",
        "proxy_name": "test_pass_rate",
        "proxy_description": "Code compiles, tests pass, benchmark score improves",
        "reality_name": "intended_outcome",
        "reality_description": "Real-world intended outcome actually achieved",
        "diagnostic_question": "Does passing this test mean the problem is actually solved, or only that the test is satisfied?",
    },
    {
        "organ": "GEOX",
        "proxy_name": "interpretation_score",
        "proxy_description": "Seismic interpretation, well log analysis, prospect evaluation scores",
        "reality_name": "subsurface_truth",
        "reality_description": "Actual subsurface geology — what is really down there",
        "diagnostic_question": "Does this model fit the data because it's correct, or because I haven't measured the dimension that would falsify it?",
    },
    {
        "organ": "WEALTH",
        "proxy_name": "computed_metrics",
        "proxy_description": "Market data feeds, computed NPV/EMV, risk metrics",
        "reality_name": "capital_position",
        "reality_description": "Actual capital position, liquidity, and downside exposure",
        "diagnostic_question": "Does this number reflect current reality, or only the last time we measured?",
    },
    {
        "organ": "WELL",
        "proxy_name": "vitality_scores",
        "proxy_description": "Biometric scores, vitality indices, triadic snapshot numbers",
        "reality_name": "human_state",
        "reality_description": "Actual human state — energy, readiness, dignity, meaning",
        "diagnostic_question": "Does this score capture the human, or only the dimension I can measure?",
    },
    {
        "organ": "arifFlow",
        "proxy_name": "fq_ratio",
        "proxy_description": "FQ ratio, receipt count, step-type distribution",
        "reality_name": "metabolic_health",
        "reality_description": "Actual metabolic health — is the federation learning from its actions?",
        "diagnostic_question": "Does this ratio show learning, or only activity?",
    },
    {
        "organ": "FRAME",
        "proxy_name": "drift_signals",
        "proxy_description": "Drift signals, behavioral metrics, trend monotonicity",
        "reality_name": "institutional_drift",
        "reality_description": "Actual institutional drift — is the federation getting worse at what matters?",
        "diagnostic_question": "Does this signal reflect real degradation, or only measurement noise on a proxy?",
    },
    {
        "organ": "FED",
        "proxy_name": "route_decision",
        "proxy_description": "Model routing decision, latency, token count, cost",
        "reality_name": "task_outcome",
        "reality_description": "Actual capability-to-task fit — did the model solve the problem?",
        "diagnostic_question": "Does this model choice optimize cost, or actual task outcome?",
    },
    {
        "organ": "CHRON",
        "proxy_name": "prediction_record",
        "proxy_description": "Timestamps, prediction records, calibration statistics",
        "reality_name": "genuine_falsification",
        "reality_description": "Actual temporal ordering and genuine falsification by outcomes",
        "diagnostic_question": "Was this falsifiable before the outcome, or only interpretable after?",
    },
    {
        "organ": "HERMES",
        "proxy_name": "validated_claims",
        "proxy_description": "Validated claims, qualia boundary classifications",
        "reality_name": "human_experience",
        "reality_description": "Actual human experience — what the person is really feeling, meaning, needing",
        "diagnostic_question": "Does this validated claim capture the human, or only what I can access about them?",
    },
    {
        "organ": "arifOS",
        "proxy_name": "session_binding",
        "proxy_description": "Session binding, SCT token validity, identity hash match",
        "reality_name": "current_authority",
        "reality_description": "Actual identity and authority of the actor at execution time",
        "diagnostic_question": "Does this token prove current authority, or only past authorization?",
    },
    {
        "organ": "A-FORGE",
        "proxy_name": "receipt_chain",
        "proxy_description": "Command exit code, receipt minted, hash chain intact",
        "reality_name": "system_state_change",
        "reality_description": "Actual system state change — did the mutation produce the intended effect?",
        "diagnostic_question": "Does the receipt prove the effect, or only that the command ran?",
    },
    {
        "organ": "888-APEX",
        "proxy_name": "floor_scores",
        "proxy_description": "Floor evaluation scores, pass/fail counts, checklist completion",
        "reality_name": "constitutional_fitness",
        "reality_description": "Constitutional fitness — whether the action is actually safe and wise",
        "diagnostic_question": "Do these floor scores reflect constitutional safety, or only formal compliance?",
    },
    {
        "organ": "555-ASI",
        "proxy_name": "multimodal_confidence",
        "proxy_description": "Multimodal analysis output, confidence scores, classification labels",
        "reality_name": "actual_content",
        "reality_description": "Actual content, meaning, and provenance of the image/audio/video",
        "diagnostic_question": "Does my confidence reflect the evidence, or only the fluency of my output?",
    },
    {
        "organ": "AAA",
        "proxy_name": "dashboard_status",
        "proxy_description": "Displayed organ status, agent registry, cockpit dashboard",
        "reality_name": "organ_state",
        "reality_description": "Actual organ state — is each organ really doing what the dashboard says?",
        "diagnostic_question": "Does this dashboard show reality, or only the last successful probe?",
    },
]


def seed_defaults() -> int:
    """Register default pairs if none exist. Returns count registered."""
    if _load_pairs():
        return 0  # already seeded
    count = 0
    for p in DEFAULT_PAIRS:
        register_pair(**p)
        count += 1
    return count
