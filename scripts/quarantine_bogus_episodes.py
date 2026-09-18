"""Quarantine episode rows that assert predictions which never existed.

Why: `episodes.jsonl` is CHRON's factual ledger — it answers "what did we
expect, what happened, were we wrong?". A `predict` episode naming a
prediction_id that is absent from `predictions.jsonl` is a false record in that
ledger, and it inflates `functions()["predict"]` for any reader.

Two sources, both measured:
  * 6 rows at 05:11/05:47 UTC — pre-existing, from a dev cycle during CHRON's
    construction earlier the same day.
  * 24 rows at 15:50/15:51 UTC — created by /root/chron/tests/test_loop_closure.py
    before it was made hermetic (a later section restored the real
    generate_from_chron_events, so the suite wrote real predictions and real
    predict episodes). The suite is fixed; verified hermetically by hashing
    episodes.jsonl / index.json / predictions.jsonl around a full run.

Append-only is preserved for real events: these rows are not records of anything
that happened, they are harness artefacts. The ledger is backed up first, the
index is rebuilt by the module's own `_rebuild_index()`, and every removed id is
written to a manifest so the removal is auditable and reversible.

Run: /opt/arifos/venv/bin/python3 /root/chron/scripts/quarantine_bogus_episodes.py [--apply]
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("/root/chron/data")
EPISODES = DATA / "episodes.jsonl"
INDEX = DATA / "index.json"
PREDICTIONS = DATA / "predictions.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    apply = "--apply" in sys.argv
    if not EPISODES.exists():
        print("no episodes file")
        return 1

    store_ids = {
        json.loads(l)["prediction_id"]
        for l in PREDICTIONS.read_text().splitlines()
        if l.strip()
    }

    kept: list[str] = []
    removed: list[dict] = []
    for line in EPISODES.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if e.get("function") != "predict":
            kept.append(line)
            continue
        bad = [
            p.get("prediction_id")
            for p in (e.get("predictions") or [])
            if p.get("prediction_id") and p.get("prediction_id") not in store_ids
        ]
        if bad:
            removed.append({
                "episode_id": e.get("episode_id"),
                "known_at": e.get("known_at"),
                "unknown_prediction_ids": bad,
                "reason": "predict episode names prediction_ids absent from predictions.jsonl",
            })
            continue
        kept.append(line)

    print(f"total rows       : {len(kept) + len(removed)}")
    print(f"kept             : {len(kept)}")
    print(f"quarantined      : {len(removed)}")

    if not removed:
        print("nothing to do")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = DATA / f"quarantine-manifest-{stamp}.json"
    manifest.write_text(json.dumps({
        "created_at": now_iso(),
        "applied": apply,
        "criterion": "predict episode whose prediction_id is absent from predictions.jsonl",
        "count": len(removed),
        "entries": removed,
    }, indent=2) + "\n")
    print(f"manifest         : {manifest}")

    if not apply:
        print("\nDRY RUN — re-run with --apply to write.")
        return 0

    backup = DATA / f".backup-episodes-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EPISODES, backup / "episodes.jsonl")
    if INDEX.exists():
        shutil.copy2(INDEX, backup / "index.json")
    print(f"backup           : {backup}")

    tmp = DATA / "episodes.jsonl.tmp"
    tmp.write_text("\n".join(kept) + "\n")
    before = hashlib.sha256(EPISODES.read_bytes()).hexdigest()[:16]
    tmp.replace(EPISODES)

    # Drop the stale index so the module rebuilds it from the filtered JSONL
    # using its own code path rather than a hand-written one.
    if INDEX.exists():
        INDEX.unlink()
    print(f"episodes hash    : {before} -> {hashlib.sha256(EPISODES.read_bytes()).hexdigest()[:16]}")

    sys.path.insert(0, "/root")
    from chron.chron_store import ChronStore

    store = ChronStore()
    print(f"index rebuilt    : episodes={store.count()}")
    print(f"by_function      : {store.functions()}")
    print("\nAPPLIED — manifest records every removed id.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
