"""CHRON NATS — subscriber for organ events.

Wires CHRON as a consumer of NATS events from the federation.
When an organ publishes a material change, CHRON observes it
and creates an observe episode.

NATS subjects:
  - chron.observe.>  — material changes from organs
  - chron.verify.>   — verification requests
  - chron.learn.>    — lesson extraction triggers

CHRON subscribes, never publishes to organ subjects.
CHRON publishes only to chron.internal.> for its own coordination.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

try:
    import nats
    from nats.aio.client import Client as NATSClient

    HAS_NATS = True
except ImportError:
    NATSClient = None  # type: ignore[assignment,misc]
    HAS_NATS = False

from chron.chron_store import get_store
from chron.chron_episode import observe_from_event

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ───────────────────────── HANDLERS ─────────────────────────


async def handle_observe(msg):
    """Handle an observation event from NATS."""
    try:
        data = json.loads(msg.data.decode())
    except Exception:
        return

    # Create observe episode
    event = {
        "id": data.get("id", f"nats-{msg.reply or 'unknown'}"),
        "title": data.get("title", data.get("content", "NATS event")),
        "target_date": data.get(
            "target_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "audience": data.get("audience", "both"),
        "kind": data.get("kind", "NATS_EVENT"),
        "source": f"nats:{msg.subject}",
        "confidence": data.get("confidence", "CONFIRMED"),
        "consequence": data.get("consequence", "MEDIUM"),
        "actionability": data.get("actionability", "WATCH"),
    }

    ep = observe_from_event(event)
    store = get_store()
    store.append(ep)

    print(f"[CHRON NATS] Observed: {event['title'][:50]} → {ep['episode_id']}")


async def handle_verify(msg):
    """Handle a verification request."""
    try:
        data = json.loads(msg.data.decode())
    except Exception:
        return

    from chron_verify import run_verification

    result = run_verification()
    print(
        f"[CHRON NATS] Verify: {result.get('due', 0)} due, "
        f"{result.get('verified_correct', 0)} correct"
    )


async def handle_learn(msg):
    """Handle a lesson extraction trigger."""
    from chron_learn import extract_lessons

    lessons = extract_lessons()
    print(f"[CHRON NATS] Learn: {len(lessons)} lessons extracted")


# ───────────────────────── SUBSCRIBER ─────────────────────────


async def subscribe():
    """Subscribe to CHRON NATS subjects."""
    if not HAS_NATS or NATSClient is None:
        print("[CHRON NATS] nats-py not installed — NATS subscriber disabled")
        return

    nc = NATSClient()
    try:
        await nc.connect(NATS_URL)
        print(f"[CHRON NATS] Connected to {NATS_URL}")

        await nc.subscribe("chron.observe.>", cb=handle_observe)
        await nc.subscribe("chron.verify.>", cb=handle_verify)
        await nc.subscribe("chron.learn.>", cb=handle_learn)

        print(
            "[CHRON NATS] Subscribed to chron.observe.>, chron.verify.>, chron.learn.>"
        )
        print("[CHRON NATS] Listening...")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        print(f"[CHRON NATS] Error: {e}")
    finally:
        if nc.is_connected:
            await nc.close()


def main():
    """CLI entry point."""
    import sys

    if "--check" in sys.argv:
        if HAS_NATS:
            print("nats-py: installed")
            print(f"NATS URL: {NATS_URL}")
        else:
            print("nats-py: NOT installed (pip install nats-py)")
        return 0

    if not HAS_NATS:
        print("nats-py not installed. Install with: pip install nats-py")
        return 1

    asyncio.run(subscribe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
