"""CHRON Attention Debt — measures unresolved contradiction backlog.

Attention Debt (AD) = Σ (known_contradiction_age_days × consequence_weight)

The core insight: systems die not because the binding constraint was unknown,
but because attention to it was deferred. AD measures the accumulating cost
of that deferral.

Sources of attention debt:
  - Overdue predictions (past verify_at, not verified)
  - SABAR verdicts (world hasn't produced data yet — legitimate but aging)
  - Unresolved anomalies (events with consequence=HIGH, no outcome recorded)
  - Calibration decay (prediction error increasing without investigation)

Lifecycle:
  IDENTIFIED → OPEN → AGING → RESOLVED | IRRELEVANT

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

DEBT_FILE = Path("/root/chron/data/attention_debt.jsonl")

CONSEQUENCE_WEIGHT = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
DEFAULT_WEIGHT = 2.0

_now_iso = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── DEBT ITEM ─────────────────────────


def _make_debt_id(source: str, ref_id: str) -> str:
    """Deterministic debt ID from source + reference."""
    import hashlib
    h = hashlib.sha256(f"{source}:{ref_id}".encode()).hexdigest()[:8]
    return f"ad-{h}"


def create_debt_item(
    source: str,
    ref_id: str,
    description: str,
    consequence: str = "MEDIUM",
    identified_at: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Create an attention debt item."""
    item = {
        "debt_id": _make_debt_id(source, ref_id),
        "source": source,
        "ref_id": ref_id,
        "description": description,
        "consequence": consequence,
        "weight": CONSEQUENCE_WEIGHT.get(consequence, DEFAULT_WEIGHT),
        "status": "OPEN",
        "identified_at": identified_at or _now_iso(),
        "resolved_at": None,
        "resolution": None,
    }
    if extra:
        item.update(extra)
    return item


# ───────────────────────── STORE ─────────────────────────


def _load_all_debt() -> list[dict]:
    """Load all debt items from JSONL."""
    if not DEBT_FILE.exists():
        return []
    items = []
    with open(DEBT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return items


def _append_debt(item: dict) -> None:
    """Append a debt item to JSONL store."""
    DEBT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DEBT_FILE, "a") as f:
        f.write(json.dumps(item, default=str) + "\n")


def _resolve_debt(debt_id: str, resolution: str) -> Optional[dict]:
    """Mark a debt item as resolved. Append new state."""
    items = _load_all_debt()
    target = None
    for item in items:
        if item.get("debt_id") == debt_id and item.get("status") not in ("RESOLVED", "IRRELEVANT"):
            target = item
    if not target:
        return None
    resolved = {**target, "status": "RESOLVED", "resolved_at": _now_iso(), "resolution": resolution}
    _append_debt(resolved)
    return resolved


# ───────────────────────── COLLECTORS ─────────────────────────


def collect_overdue_predictions() -> list[dict]:
    """Collect predictions past verify_at that haven't been verified."""
    from chron.chron_prediction import get_active

    now = datetime.now(timezone.utc)
    items = []
    for pred in get_active():
        try:
            verify_at = datetime.fromisoformat(pred["verify_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if verify_at > now:
            continue  # not overdue

        overdue_days = (now - verify_at).total_seconds() / 86400
        items.append(create_debt_item(
            source="overdue_prediction",
            ref_id=pred["prediction_id"],
            description=f"Prediction overdue by {overdue_days:.0f}d: {pred.get('claim', '?')[:80]}",
            consequence="HIGH" if overdue_days > 30 else "MEDIUM",
            identified_at=verify_at.isoformat(),
            extra={
                "overdue_days": round(overdue_days, 1),
                "original_confidence": pred.get("confidence"),
                "claim": pred.get("claim"),
            },
        ))
    return items


def collect_unresolved_high_events() -> list[dict]:
    """Collect HIGH-consequence events with no outcome recorded."""
    events_path = Path("/root/AAA/scripts/chron_events.json")
    if not events_path.exists():
        return []

    data = json.loads(events_path.read_text())
    events = data.get("events", [])
    now = datetime.now(timezone.utc)
    items = []

    for event in events:
        if event.get("consequence") != "HIGH":
            continue
        target_date = event.get("target_date")
        if not target_date:
            continue
        try:
            target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        # Only flag events whose target date has passed
        if target > now:
            continue

        age_days = (now - target).total_seconds() / 86400
        items.append(create_debt_item(
            source="unresolved_event",
            ref_id=event.get("id", "?"),
            description=f"HIGH event past target by {age_days:.0f}d: {event.get('title', '?')[:80]}",
            consequence="HIGH",
            identified_at=target.isoformat(),
            extra={
                "age_days": round(age_days, 1),
                "event_kind": event.get("kind"),
                "original_confidence": event.get("confidence"),
            },
        ))
    return items


# ───────────────────────── CALCULATOR ─────────────────────────


def compute_attention_debt(items: Optional[list[dict]] = None) -> dict:
    """Compute Attention Debt from collected items.

    AD(t) = Σ (age_days × consequence_weight)

    Returns:
        total_ad: float — the total attention debt score
        item_count: int — number of open debt items
        items: list — individual debt items with age and weighted score
        by_source: dict — breakdown by source type
        by_consequence: dict — breakdown by consequence level
        oldest: dict — the oldest unresolved item
        growth_rate: float — AD change rate per day (if historical data exists)
    """
    if items is None:
        items = collect_current_debt()

    now = datetime.now(timezone.utc)
    scored = []
    by_source = {}
    by_consequence = {"HIGH": {"count": 0, "ad": 0.0}, "MEDIUM": {"count": 0, "ad": 0.0}, "LOW": {"count": 0, "ad": 0.0}}

    for item in items:
        try:
            identified = datetime.fromisoformat(item["identified_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue

        age_days = max(0, (now - identified).total_seconds() / 86400)
        weight = item.get("weight", DEFAULT_WEIGHT)
        ad_contribution = age_days * weight

        scored_item = {
            **item,
            "age_days": round(age_days, 1),
            "ad_contribution": round(ad_contribution, 2),
        }
        scored.append(scored_item)

        # Aggregate by source
        src = item.get("source", "unknown")
        if src not in by_source:
            by_source[src] = {"count": 0, "ad": 0.0}
        by_source[src]["count"] += 1
        by_source[src]["ad"] += ad_contribution

        # Aggregate by consequence
        cons = item.get("consequence", "MEDIUM")
        if cons in by_consequence:
            by_consequence[cons]["count"] += 1
            by_consequence[cons]["ad"] += ad_contribution

    total_ad = sum(s["ad_contribution"] for s in scored)
    oldest = max(scored, key=lambda x: x["age_days"], default=None)

    # Compute growth rate from historical snapshots
    growth_rate = _compute_growth_rate(total_ad)

    return {
        "total_ad": round(total_ad, 2),
        "item_count": len(scored),
        "items": scored,
        "by_source": {k: {"count": v["count"], "ad": round(v["ad"], 2)} for k, v in by_source.items()},
        "by_consequence": {k: {"count": v["count"], "ad": round(v["ad"], 2)} for k, v in by_consequence.items()},
        "oldest": {
            "debt_id": oldest["debt_id"],
            "description": oldest["description"],
            "age_days": oldest["age_days"],
            "ad_contribution": oldest["ad_contribution"],
        } if oldest else None,
        "growth_rate_per_day": growth_rate,
        "computed_at": _now_iso(),
    }


def collect_current_debt() -> list[dict]:
    """Collect all current unresolved debt items from live sources."""
    # Deduplicate by debt_id — keep latest state
    all_items = _load_all_debt()
    seen = {}
    for item in all_items:
        did = item.get("debt_id")
        if did:
            seen[did] = item

    # Filter to only OPEN items (not resolved)
    open_items = [v for v in seen.values() if v.get("status") == "OPEN"]

    # Merge with fresh collection (new items only)
    existing_ids = {item.get("debt_id") for item in open_items}
    fresh = []
    for collector in [collect_overdue_predictions, collect_unresolved_high_events]:
        try:
            for item in collector():
                if item["debt_id"] not in existing_ids:
                    fresh.append(item)
                    existing_ids.add(item["debt_id"])
        except Exception:
            continue

    # Persist new items
    for item in fresh:
        _append_debt(item)

    return open_items + fresh


# ───────────────────────── GROWTH RATE ─────────────────────────

AD_SNAPSHOT_FILE = Path("/root/chron/data/ad_snapshots.jsonl")


def _snapshot_ad(total_ad: float, item_count: int) -> None:
    """Record an AD snapshot for trend computation."""
    AD_SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "total_ad": total_ad,
        "item_count": item_count,
        "recorded_at": _now_iso(),
    }
    with open(AD_SNAPSHOT_FILE, "a") as f:
        f.write(json.dumps(snapshot, default=str) + "\n")


def _compute_growth_rate(current_ad: float) -> Optional[float]:
    """Compute AD growth rate from historical snapshots.

    Returns: AD change per day (positive = growing debt, negative = resolving).
    None if insufficient history.
    """
    if not AD_SNAPSHOT_FILE.exists():
        # Record first snapshot
        _snapshot_ad(current_ad, 0)
        return None

    snapshots = []
    with open(AD_SNAPSHOT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    s = json.loads(line)
                    s["_ts"] = datetime.fromisoformat(s["recorded_at"].replace("Z", "+00:00"))
                    snapshots.append(s)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    if len(snapshots) < 2:
        _snapshot_ad(current_ad, 0)
        return None

    # Use oldest and newest snapshot
    snapshots.sort(key=lambda x: x["_ts"])
    oldest = snapshots[0]
    newest = snapshots[-1]
    dt_days = (newest["_ts"] - oldest["_ts"]).total_seconds() / 86400
    if dt_days < 0.01:
        return None

    growth = (newest["total_ad"] - oldest["total_ad"]) / dt_days

    # Record current snapshot
    _snapshot_ad(current_ad, 0)

    return round(growth, 4)


# ───────────────────────── PUBLIC API ─────────────────────────


def get_attention_debt() -> dict:
    """Full Attention Debt report for MCP surface."""
    items = collect_current_debt()
    result = compute_attention_debt(items)

    # Record snapshot for trend tracking
    _snapshot_ad(result["total_ad"], result["item_count"])

    return result


def get_ad_summary() -> dict:
    """Compact AD summary for briefing injection."""
    items = collect_current_debt()
    result = compute_attention_debt(items)
    return {
        "total_ad": result["total_ad"],
        "item_count": result["item_count"],
        "oldest": result["oldest"],
        "growth_rate_per_day": result["growth_rate_per_day"],
    }
