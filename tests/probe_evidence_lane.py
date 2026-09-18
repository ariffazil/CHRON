"""Probe the evidence-acquisition lane directly. Read-only: performs searches,
writes nothing. Evidence path: /root/chron/REPAIR-RECEIPT-2026-09-18.md
"""
import json
import sys

sys.path.insert(0, "/root")
from chron.chron_verify import _evidence_for, _search  # noqa: E402

print("=== A. search lane reachable? ===")
res = _search("Malaysia budget 2026 tabled parliament")
print("results:", len(res))
if res:
    print("first title:", str(res[0].get("title"))[:90])
    print("first url  :", str(res[0].get("url"))[:100])

print()
print("=== B. event-occurrence evidence (a real past event) ===")
ev = _evidence_for(
    {"unit": "", "threshold": None},
    {"id": "x", "title": "Malaysia Budget 2026 tabled in parliament",
     "target_date": "2025-10-10", "kind": "FISCAL"},
)
print(json.dumps(ev, indent=2) if ev else "None (declined - stays open)")

print()
print("=== C. threshold evidence (magnitude claim) ===")
ev2 = _evidence_for(
    {"unit": "MYR", "threshold": 30000000000},
    {"id": "y", "title": "PETRONAS dividend to federal government",
     "target_date": "2026-10-09", "kind": "FISCAL"},
)
print(json.dumps(ev2, indent=2)[:500] if ev2 else "None (declined - stays open)")

print()
print("=== D. nonsense claim must decline, not invent ===")
ev3 = _evidence_for(
    {"unit": "", "threshold": None},
    {"id": "z", "title": "Zxqvw plorbnic fribblet",
     "target_date": "2020-01-01", "kind": "OBSERVATION"},
)
print(json.dumps(ev3, indent=2) if ev3 else "None - correctly declines (no evidence)")
