"""FLOW → CHRON bridge.

Tail-follows arifFlow receipts JSONL, creates ChronEpisodes for new receipts.
Tracks offset to avoid re-processing.

Pattern: arifFlow writes → CHRON reads.
Invariant #6: arifFlow transports; CHRON does not.

Usage:
  python3 /root/chron/bridge_flow.py              # one-shot incremental
  python3 /root/chron/bridge_flow.py --backfill    # process all from start
  python3 /root/chron/bridge_flow.py --tail 500    # process last N receipts

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from chron_store import get_store, _make_episode_id, _now_iso

# ── Config ──────────────────────────────────────────────────────────

RECEIPTS_FILE = Path(
    os.environ.get(
        "ARIFLOW_RECEIPTS",
        "/var/lib/arifflow/receipts.jsonl",
    )
)
OFFSET_FILE = Path(os.environ.get("CHRON_OFFSET", "/root/chron/data/.flow_offset"))
EPISODES_FILE = Path(os.environ.get("CHRON_EPISODES", "/root/chron/data/episodes.jsonl"))

# Step types worth creating episodes for (skip noise)
INTERESTING_STEPS = {
    "Execute", "Verify", "Observe", "Predict", "Learn", "Seal",
    "Route", "Judge", "Ingest", "Cool",
}

# ── Helpers ─────────────────────────────────────────────────────────


def _read_offset() -> int:
    """Byte offset into receipts file."""
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip())
        except ValueError:
            return 0
    return 0


def _write_offset(offset: int):
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def _receipt_to_episode(receipt: dict) -> dict | None:
    """Convert a FlowReceipt to a ChronEpisode. Returns None if not interesting."""
    step_type = receipt.get("step_type", "Unknown")
    if step_type not in INTERESTING_STEPS:
        return None

    created = receipt.get("created_at", _now_iso())
    actor = receipt.get("actor_id", "unknown")
    session = receipt.get("session_id", "unknown")
    receipt_id = receipt.get("receipt_id", "?")
    verdict = receipt.get("floor_verdict", "UNKNOWN")
    epistemic = receipt.get("epistemic_label", "UNKNOWN")
    risk = receipt.get("risk_class", "unknown")
    payload = receipt.get("payload")

    content = f"{step_type} by {actor} in {session}"
    if verdict and verdict != "UNKNOWN":
        content += f" — verdict={verdict}"

    observations = [
        {
            "obs_id": f"flow-{receipt_id[:12]}",
            "source": f"arifFlow:{receipt_id}",
            "content": content,
            "observed_at": created,
            "truth_class": "OBS",
            "confidence": 0.95,
        }
    ]

    if payload and isinstance(payload, dict):
        payload_summary = json.dumps(payload, default=str)[:500]
        observations.append({
            "obs_id": f"flow-{receipt_id[:12]}-payload",
            "source": f"arifFlow:{receipt_id}:payload",
            "content": payload_summary,
            "observed_at": created,
            "truth_class": "OBS",
            "confidence": 0.90,
        })

    body = {
        "function": "observe",
        "question": "What happened in the federation?",
        "principal": actor,
        "audience": "shared",
        "valid_time": created,
        "observed_at": _now_iso(),
        "observations": observations,
        "claims": [],
        "source_ref": f"arifFlow:{receipt_id}",
        "metadata": {
            "step_type": step_type,
            "risk_class": risk,
            "epistemic_label": epistemic,
            "floor_verdict": verdict,
            "session_id": session,
            "bridge": "flow→chron",
        },
    }

    # Assign episode ID
    body["episode_id"] = _make_episode_id("observe", body)
    body["known_at"] = _now_iso()
    return body


# ── Bulk append (fast path) ─────────────────────────────────────────


def _bulk_append(episodes: list[dict]) -> int:
    """Append episodes directly to JSONL, then rebuild index once."""
    if not episodes:
        return 0

    EPISODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EPISODES_FILE, "a") as f:
        for ep in episodes:
            f.write(json.dumps(ep, default=str) + "\n")

    # Rebuild index once
    store = get_store()
    store._rebuild_index()
    return len(episodes)


# ── Main ────────────────────────────────────────────────────────────


def run_backfill() -> dict:
    """Process all receipts from start."""
    _write_offset(0)
    return run()


def run_tail(n: int = 500) -> dict:
    """Process last N receipts (for initial seeding)."""
    if not RECEIPTS_FILE.exists():
        return {"status": "no_file", "path": str(RECEIPTS_FILE), "new_episodes": 0}

    # Read last N lines
    import collections
    with open(RECEIPTS_FILE) as f:
        last_lines = collections.deque(f, maxlen=n)

    episodes = []
    for line in last_lines:
        line = line.strip()
        if not line:
            continue
        try:
            receipt = json.loads(line)
        except json.JSONDecodeError:
            continue
        episode = _receipt_to_episode(receipt)
        if episode:
            episodes.append(episode)

    count = _bulk_append(episodes)

    # Set offset to current file size so incremental picks up from here
    _write_offset(RECEIPTS_FILE.stat().st_size)

    return {
        "status": "tail_complete",
        "processed": len(last_lines),
        "new_episodes": count,
        "total_episodes": get_store().count(),
    }


def run() -> dict:
    """Incremental: read new receipts since last offset, create episodes."""
    if not RECEIPTS_FILE.exists():
        return {"status": "no_file", "path": str(RECEIPTS_FILE), "new_episodes": 0}

    offset = _read_offset()
    file_size = RECEIPTS_FILE.stat().st_size

    if offset >= file_size:
        return {"status": "up_to_date", "offset": offset, "new_episodes": 0}

    episodes = []

    with open(RECEIPTS_FILE) as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                receipt = json.loads(line)
            except json.JSONDecodeError:
                continue
            episode = _receipt_to_episode(receipt)
            if episode:
                episodes.append(episode)

    count = _bulk_append(episodes)
    _write_offset(RECEIPTS_FILE.stat().st_size)

    return {
        "status": "processed",
        "offset_before": offset,
        "offset_after": RECEIPTS_FILE.stat().st_size,
        "new_episodes": count,
        "total_episodes": get_store().count(),
    }


if __name__ == "__main__":
    import sys

    if "--backfill" in sys.argv:
        result = run_backfill()
    elif "--tail" in sys.argv:
        idx = sys.argv.index("--tail")
        n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 500
        result = run_tail(n)
    else:
        result = run()
    print(json.dumps(result, indent=2, default=str))
