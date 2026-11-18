"""Regression tests for chron_resolve_event (REALITY-CLOSURE-PROTOCOL v0.1, F13-ratified 2026-10-07)."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/root/chron")
import server

def _with_store(tmp_event):
    fd = Path(tempfile.mkstemp(suffix=".json")[1])
    fd.write_text(json.dumps({"events": [tmp_event]}))
    old = server.CHRON_EVENTS_STORE
    server.CHRON_EVENTS_STORE = fd
    return fd, old

EV = {"id": "t-evt-1", "title": "test anchor", "consequence": "HIGH", "target_date": "2026-10-01"}
ok = err = 0
# 1 evidence required
fd, old = _with_store(dict(EV))
r = server.chron_resolve_event("t-evt-1", "x", "  ")
assert r["error_code"] == "EVIDENCE_REQUIRED"; ok += 1
# 2 not found
r = server.chron_resolve_event("nope", "x", "/tmp/e")
assert r["error_code"] == "EVENT_NOT_FOUND"; ok += 1
# 3 happy path + debt close (no matching debt items → empty list, no error)
r = server.chron_resolve_event("t-evt-1", "FULFILLED", "/tmp/evidence.md")
assert r["status"] == "RESOLVED" and r["debt_ledger_error"] is None; ok += 1
# 4 idempotent
r = server.chron_resolve_event("t-evt-1", "again", "/tmp/e")
assert r["status"] == "ALREADY_RESOLVED"; ok += 1
# 5 persisted record carries evidence
rec = json.loads(fd.read_text())["events"][0]["resolution"]
assert rec["evidence_ref"] == "/tmp/evidence.md"; ok += 1
server.CHRON_EVENTS_STORE = old; fd.unlink()
# 6 discharge: attention debt skips resolved events
from chron.chron_attention_debt import collect_unresolved_high_events
assert all(i["ref_id"] != "at010-eval-contract-frozen-30d45853" for i in collect_unresolved_high_events()); ok += 1
print(f"resolve_event tests: {ok}/6 passed, {err} failed")
sys.exit(1 if err else 0)
