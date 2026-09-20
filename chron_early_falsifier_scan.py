#!/usr/bin/env python3
"""CHRON Early-Falsifier Scan — report-only, no ledger writes.

Purpose: a prediction whose falsifier is ALREADY satisfied before its verify_at
is invisible to a date-keyed verifier. This scanner asks the *condition*
question instead of the *calendar* question.

State vocabulary (deliberately not collapsing into FALSIFIED):

    FALSIFIER_ALREADY_SATISFIED  — condition already contradicted; date not reached
    SIGNAL_PRESENT               — condition still consistent with the claim
    HUMAN_WITNESS_REQUIRED       — audience=arif and no machine probe
    AWAITING_EVIDENCE            — no probe registered for this claim's source
    DUE                          — date reached; defer to the normal verifier

EARLY_FALSIFIER_SATISFIED != FALSIFIED. Nothing here mutates a verdict.

DITEMPA BUKAN DIBERI
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

PREDICTIONS = pathlib.Path("/root/chron/data/predictions.jsonl")
OIL_SNAPSHOT = "http://127.0.0.1:3457/api/snapshot"
F13_PACKETS = pathlib.Path("/root/AAA/governance/f13-packets")
# Inclusive of W38-3 (mtime 2026-09-16 03:03:56 MYT). The claim's own
# verifier named that file as baseline, not as a new packet.
PACKET_BASELINE = datetime(2026, 9, 16, 3, 4, tzinfo=timezone(timedelta(hours=8)))


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def probe_systemd_venv(claim: str):
    """Claim contract: 'N ExecStart lines point at /opt/arifos/venv ... will still point there'.

    Returns (state, observed) or None if this probe does not own the claim.
    """
    if "/opt/arifos/venv" not in claim:
        return None
    out = subprocess.run(
        ["bash", "-lc",
         "grep -rl '/opt/arifos/venv' /etc/systemd/system/*.service 2>/dev/null | wc -l"],
        capture_output=True, text=True, timeout=60,
    )
    count = int((out.stdout or "0").strip() or 0)
    observed = f"{count} live unit file(s) still reference /opt/arifos/venv"
    # The claim asserts the broken lines PERSIST. Falsifier: they are gone.
    state = "FALSIFIER_ALREADY_SATISFIED" if count == 0 else "SIGNAL_PRESENT"
    return state, observed


def probe_brent_band(claim: str):
    """Claim contract: 'Brent crude will be in range USD65-75/bbl on <date>'."""
    low, high = 65.0, 75.0
    if "Brent" not in claim or "65-75" not in claim:
        return None
    try:
        with urllib.request.urlopen(OIL_SNAPSHOT, timeout=30) as r:
            snap = json.loads(r.read().decode())
    except Exception as exc:  # lane down is not evidence either way
        return "AWAITING_EVIDENCE", f"oil lane unreachable: {exc}"
    price = (snap.get("ticker") or {}).get("price")
    if price is None:
        return "AWAITING_EVIDENCE", "oil lane returned no price"
    observed = f"XBRENT={price} at {snap.get('observed_at')}"
    # A reading today is a leading indicator, NOT a verdict on a later window.
    state = "FALSIFIER_ALREADY_SATISFIED" if not (low <= float(price) <= high) else "SIGNAL_PRESENT"
    return state, observed


def probe_weekly_packet(claim: str):
    """Claim: f13-weekly-packet produces 0 new packet files by 2026-09-28.

    Baseline on disk: 3 files, newest mtime 2026-09-16 03:03 MYT.
    Falsifier: any file newer than that baseline.
    """
    if "f13-weekly-packet" not in claim and "f13-packet" not in claim:
        return None
    if not F13_PACKETS.is_dir():
        return "AWAITING_EVIDENCE", "f13-packets dir missing"
    newer = []
    for p in F13_PACKETS.iterdir():
        if not p.is_file():
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        if mtime > PACKET_BASELINE:
            newer.append(p.name)
    observed = f"{len(newer)} packet file(s) newer than baseline W38-3 (2026-09-16 03:03:56 MYT)"
    state = "FALSIFIER_ALREADY_SATISFIED" if newer else "SIGNAL_PRESENT"
    return state, observed


def probe_tree777_crontab(claim: str):
    """Claim: TREE777 per-agent weekly anchor remains absent from every crontab."""
    if "TREE777" not in claim and "tree777" not in claim:
        return None
    hits = []
    for path in ("/etc/cron.d", "/etc/crontab"):
        out = subprocess.run(
            ["bash", "-lc", f"grep -rni 'tree777' {path} 2>/dev/null || true"],
            capture_output=True, text=True, timeout=30,
        )
        text = (out.stdout or "").strip()
        if text:
            hits.append(path)
    root_cron = subprocess.run(
        ["bash", "-lc", "crontab -l 2>/dev/null | grep -i tree777 || true"],
        capture_output=True, text=True, timeout=15,
    )
    if (root_cron.stdout or "").strip():
        hits.append("root-crontab")
    observed = (
        f"tree777 matches in: {', '.join(hits)}" if hits else "tree777 absent from crontab surfaces"
    )
    # Claim asserts ABSENCE. Falsifier: a match appears.
    state = "FALSIFIER_ALREADY_SATISFIED" if hits else "SIGNAL_PRESENT"
    return state, observed


PROBES = (probe_systemd_venv, probe_brent_band, probe_weekly_packet, probe_tree777_crontab)


def main() -> int:
    now = datetime.now(timezone.utc)
    rows = [json.loads(line) for line in PREDICTIONS.read_text().splitlines() if line.strip()]

    buckets: dict[str, list] = {}
    for row in rows:
        due = _parse(row.get("verify_at"))
        status = (row.get("status") or "").upper()
        if status in ("CORRECT", "FALSIFIED", "RESOLVED", "SCORED"):
            continue
        if due is None:
            continue
        if due <= now:
            buckets.setdefault("DUE", []).append((row, "verify_at reached"))
            continue

        state, observed = "AWAITING_EVIDENCE", "no probe registered for this source"
        for probe in PROBES:
            result = probe(row.get("claim", ""))
            if result:
                state, observed = result
                break
        if state == "AWAITING_EVIDENCE" and (row.get("audience") or "") == "arif":
            state, observed = "HUMAN_WITNESS_REQUIRED", "audience=arif; no machine probe"
        buckets.setdefault(state, []).append((row, observed))

    n = sum(len(v) for v in buckets.values())
    awaiting = len(buckets.get("AWAITING_EVIDENCE", []))
    human = len(buckets.get("HUMAN_WITNESS_REQUIRED", []))
    early = len(buckets.get("FALSIFIER_ALREADY_SATISFIED", []))
    print(f"CHRON EARLY-FALSIFIER SCAN — {now.isoformat(timespec='seconds')}")
    print(f"unscored predictions with a verify date: {n}\n")
    for state in (
        "FALSIFIER_ALREADY_SATISFIED",
        "DUE",
        "SIGNAL_PRESENT",
        "HUMAN_WITNESS_REQUIRED",
        "AWAITING_EVIDENCE",
    ):
        items = buckets.get(state, [])
        print(f"[{state}] {len(items)}")
        for row, observed in items:
            due = str(row.get("verify_at"))[:10]
            claim = (row.get("claim") or "")[:88]
            print(f"   due {due} | {claim}")
            print(f"      observed: {observed}")
        print()

    report = {
        "scanned_at": now.isoformat(),
        "unscored": n,
        "early_falsifier": early,
        "awaiting_machine_probe": awaiting,
        "human_witness_required": human,
        "share_unprobed": (awaiting / n) if n else None,
        "note": "AWAITING_EVIDENCE = no registered machine probe AND not audience=arif. HUMAN_WITNESS_REQUIRED = audience=arif with no machine probe. Some calendar claims (budget day, BNM) stay human. EARLY_FALSIFIER_SATISFIED != FALSIFIED.",
        "buckets": {
            k: [
                {"prediction_id": r.get("prediction_id"), "claim": (r.get("claim") or "")[:120], "observed": o}
                for r, o in items
            ]
            for k, items in buckets.items()
        },
    }
    out = pathlib.Path("/root/chron/data/binding_scan.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
