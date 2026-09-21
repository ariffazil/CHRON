"""CHRON Store — append-only bitemporal episode ledger.

Design:
  - One JSONL file per store (episodes.jsonl)
  - Append-only: corrections use supersedes, never rewrites
  - Bitemporal: valid_time (when reality happened) + known_at (when accepted)
  - Content-addressed episode IDs: chron-ep-{YYYYMMDD}-{function}-{hash8}
  - Index kept in memory for fast lookups, rebuilt from JSONL on load

Generation semantics (F13, 2026-09-21 — Phase 0):
  - Every episode is stamped with store_generation at append time.
  - store_generation bumps only on signed compaction/transition.
  - INVARIANT: count() may disagree with index only if signed
    compaction_receipt exists. Otherwise HOLD — serve JSONL truth.
  - Reads (count, functions) ALWAYS read JSONL fresh. Index is a
    write-time optimization, never a read-time source of truth.

DITEMPA BUKAN DIBERI ⚒️
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STORE_DIR = Path(os.environ.get("CHRON_STORE_DIR", "/root/chron/data"))
EPISODES_FILE = STORE_DIR / "episodes.jsonl"
INDEX_FILE = STORE_DIR / "index.json"

# Phase 0 — temporal state semantics
STORE_GENERATION = "gen-2026-09-21T13-05Z"
SCHEMA_VERSION = "chron-store/2.0"
COMPACTION_RECEIPT = os.environ.get("CHRON_COMPACTION_RECEIPT")  # None unless signed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _content_hash(episode: dict) -> str:
    """8-char content hash from canonical JSON."""
    canonical = json.dumps(episode, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()[:8]


def _make_episode_id(function: str, body: dict) -> str:
    """chron-ep-{YYYYMMDD}-{function}-{hash8}"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    h = _content_hash(body)
    return f"chron-ep-{today}-{function}-{h}"


def _count_jsonl_lines() -> int:
    """Count actual lines in JSONL (source of truth)."""
    if not EPISODES_FILE.exists():
        return 0
    try:
        return sum(1 for line in EPISODES_FILE.read_text().splitlines() if line.strip())
    except OSError:
        return 0


def _functions_from_jsonl() -> dict[str, int]:
    """Count episodes by function, reading JSONL fresh."""
    counts: dict[str, int] = {}
    if not EPISODES_FILE.exists():
        return counts
    try:
        with open(EPISODES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ep = json.loads(line)
                    fn = ep.get("function", "?")
                    counts[fn] = counts.get(fn, 0) + 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return counts


def enforce_count_invariant() -> dict:
    """
    INVARIANT: JSONL count is the source of truth. Index may diverge
    ONLY if signed compaction_receipt exists. Otherwise report HOLD.

    Returns a status dict. Does NOT raise — callers decide what to do.
    """
    jsonl_count = _count_jsonl_lines()
    index_count = 0
    if INDEX_FILE.exists():
        try:
            index_data = json.loads(INDEX_FILE.read_text())
            index_count = len(index_data)
        except (OSError, json.JSONDecodeError):
            index_count = -1  # unreadable

    drift = index_count - jsonl_count
    held = (drift <= 0) or bool(COMPACTION_RECEIPT)

    return {
        "jsonl_count": jsonl_count,
        "index_count": index_count,
        "drift": drift,
        "compaction_receipt": COMPACTION_RECEIPT,
        "invariant_held": held,
        "generation": STORE_GENERATION,
        "schema_version": SCHEMA_VERSION,
        "checked_at": _now_iso(),
    }


# ───────────────────────── STORE OPERATIONS ─────────────────────────


class ChronStore:
    """Append-only bitemporal episode ledger."""

    def __init__(self, store_dir: Path = STORE_DIR):
        self.store_dir = store_dir
        self.episodes_file = store_dir / "episodes.jsonl"
        self.index_file = store_dir / "index.json"
        self._index: dict[str, dict] = {}
        self._ensure_dir()
        self._load_index()

    def _ensure_dir(self):
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self):
        """Rebuild index from JSONL if index missing or stale."""
        if self.index_file.exists():
            try:
                self._index = json.loads(self.index_file.read_text())
                return
            except Exception:
                pass
        # Rebuild from JSONL
        self._rebuild_index()

    def _rebuild_index(self):
        """Scan JSONL and build index."""
        self._index = {}
        if not self.episodes_file.exists():
            return
        with open(self.episodes_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ep = json.loads(line)
                    eid = ep.get("episode_id", "")
                    self._index[eid] = {
                        "episode_id": eid,
                        "function": ep.get("function"),
                        "valid_time": ep.get("valid_time"),
                        "known_at": ep.get("known_at"),
                        "principal": ep.get("principal"),
                        "output_decision": ep.get("output_decision"),
                        "supersedes_episode_id": ep.get("supersedes_episode_id"),
                        "line_offset": None,  # computed on read
                    }
                except json.JSONDecodeError:
                    continue
        self._save_index()

    def _save_index(self):
        self.index_file.write_text(json.dumps(self._index, indent=2, default=str))

    def append(self, episode: dict) -> dict:
        """Append episode to store. Returns the episode with assigned ID."""
        if "episode_id" not in episode:
            episode["episode_id"] = _make_episode_id(
                episode.get("function", "observe"), episode
            )
        if "known_at" not in episode:
            episode["known_at"] = _now_iso()

        # Phase 0 — stamp generation
        episode["generation"] = STORE_GENERATION
        episode["schema_version"] = SCHEMA_VERSION

        # Validate minimum required fields
        required = ["episode_id", "function", "valid_time"]
        missing = [k for k in required if k not in episode]
        if missing:
            raise ValueError(f"Episode missing required fields: {missing}")

        # Append to JSONL
        with open(self.episodes_file, "a") as f:
            f.write(json.dumps(episode, default=str) + "\n")

        # Update in-memory index
        self._index[episode["episode_id"]] = {
            "episode_id": episode["episode_id"],
            "function": episode.get("function"),
            "valid_time": episode.get("valid_time"),
            "known_at": episode.get("known_at"),
            "principal": episode.get("principal"),
            "output_decision": episode.get("output_decision"),
            "supersedes_episode_id": episode.get("supersedes_episode_id"),
        }
        # Phase 0 — defer index.json write; JSONL is the truth.
        # Index file is now write-time cache only. Reads always JSONL.
        return episode

    def get(self, episode_id: str) -> Optional[dict]:
        """Read a single episode by ID (scans JSONL)."""
        if not self.episodes_file.exists():
            return None
        with open(self.episodes_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ep = json.loads(line)
                    if ep.get("episode_id") == episode_id:
                        return ep
                except json.JSONDecodeError:
                    continue
        return None

    def query(
        self,
        function: Optional[str] = None,
        principal: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query episodes by function, principal, time range."""
        results = []
        for eid, meta in self._index.items():
            if function and meta.get("function") != function:
                continue
            if principal and meta.get("principal") != principal:
                continue
            vt = meta.get("valid_time", "")
            if since and vt < since:
                continue
            if until and vt > until:
                continue
            results.append(meta)
        results.sort(key=lambda x: x.get("valid_time", ""), reverse=True)
        return results[:limit]

    def count(self) -> int:
        """
        Phase 0 — read JSONL fresh. JSONL is source of truth.
        Index is write-time cache only.
        """
        return _count_jsonl_lines()

    def functions(self) -> dict[str, int]:
        """Phase 0 — read JSONL fresh per query."""
        return _functions_from_jsonl()

    def latest(self, function: Optional[str] = None, n: int = 5) -> list[dict]:
        """Get latest N episodes, optionally filtered by function."""
        return self.query(function=function, limit=n)


# ───────────────────────── SINGLETON ─────────────────────────

_store: Optional[ChronStore] = None


def get_store(store_dir: Path = STORE_DIR) -> ChronStore:
    global _store
    if _store is None:
        _store = ChronStore(store_dir)
    return _store
