#!/usr/bin/env python3
"""
CHRON Verification Loop — closes predictions against observed reality.
Runs daily via cron. Queries predictions due, verifies against live data,
and records outcomes via chron_record_verification.

Uses FastMCP 3.x Streamable HTTP with session management.

FIRST TEST: 23 Sept 2026 (RON95 + Brent crude predictions).
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

CHRON_URL = "http://127.0.0.1:18102/mcp"
WEALTH_URL = "http://127.0.0.1:18082/mcp"
LOG_FILE = "/root/chron/verify_log.jsonl"
MYT = timezone(timedelta(hours=8))

_session_ids = {}  # base_url → session_id


def _mcp_raw(base_url, payload, session_id=None):
    """Raw JSON-RPC call with optional session ID. Returns (headers, body_dict).
    Handles both SSE (text/event-stream) and plain JSON responses."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp_headers = dict(resp.getheaders())
        raw = resp.read().decode()
    # Try SSE format first (data: lines)
    body = None
    for line in raw.split("\n"):
        if line.startswith("data: "):
            body = json.loads(line[6:])
            break
    # Fallback to plain JSON (WEALTH uses this format)
    if body is None and raw.strip():
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
    return resp_headers, body or {}


def _get_session(base_url):
    """Get or create a FastMCP 3.x session for a server."""
    if base_url in _session_ids:
        return _session_ids[base_url]
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "chron-verify", "version": "1.0"},
        },
    }
    headers, _ = _mcp_raw(base_url, payload)
    sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    if not sid:
        return None
    _session_ids[base_url] = sid
    # Send initialized notification
    _mcp_raw(base_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=sid)
    return sid


def mcp_call(base_url, tool_name, arguments):
    """Call an MCP tool with automatic session management."""
    sid = _get_session(base_url)
    payload = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    _, body = _mcp_raw(base_url, payload, session_id=sid)
    return body


def chron_call(tool_name, arguments):
    return mcp_call(CHRON_URL, tool_name, arguments)


def wealth_call(tool_name, arguments):
    return mcp_call(WEALTH_URL, tool_name, arguments)


def parse_content(result):
    """Extract text content from MCP tool result."""
    if not result:
        return ""
    content = (result.get("result", {}) or {}).get("content", [])
    for c in content:
        if c.get("type") == "text":
            return c["text"]
    return ""


def parse_json(result):
    """Parse JSON content from MCP result.
    Handles nested JSON: result.content[0].text may itself be a JSON string."""
    text = parse_content(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def extract_price_from_wealth(result, price_keys=("price", "last", "close", "current")):
    """Extract a price from WEALTH's nested response structure.
    WEALTH wraps data as: result.content[0].text = JSON string with
    result.snapshot.ticker.price structure."""
    data = parse_json(result)
    if not data:
        return None
    # Navigate WEALTH's nested structure
    snapshot = data.get("result", {}).get("snapshot", data.get("snapshot", {}))
    ticker = snapshot.get("ticker", {})
    # Try ticker first (WEALTH commodity format)
    for key in price_keys:
        val = ticker.get(key)
        if isinstance(val, (int, float)):
            return val
    # Try snapshot level
    for key in price_keys:
        val = snapshot.get(key)
        if isinstance(val, (int, float)):
            return val
    # Try top-level
    for key in price_keys:
        val = data.get(key)
        if isinstance(val, (int, float)):
            return val
    return None


def log_entry(entry):
    """Append to verification log."""
    entry["logged_at"] = datetime.now(MYT).isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Verification methods ─────────────────────────────────────────────

def verify_ron95_price(prediction):
    """Check RON95 price from WEALTH fuel commodity data."""
    try:
        result = wealth_call("capital_market", {"mode": "commodity", "commodity": "fuel"})
        price = extract_price_from_wealth(result)
        if price is not None:
            threshold = prediction.get("threshold", 2.05)
            return price <= threshold, f"RON95=RM{price:.2f}/litre (threshold ≤ RM{threshold:.2f})"
        data = parse_json(result)
        return None, f"ron95_price_not_extracted: {json.dumps(data)[:400]}" if data else "wealth_commodity_no_data"
    except Exception as e:
        return None, f"wealth_error: {e}"


def verify_brent_range(prediction):
    """Check Brent crude from WEALTH commodity data."""
    try:
        result = wealth_call("capital_market", {"mode": "oil"})
        price = extract_price_from_wealth(result)
        if price is not None:
            low = prediction.get("threshold_low", 65)
            high = prediction.get("threshold_high", 75)
            return low <= price <= high, f"Brent=USD{price:.2f}/bbl (range USD{low}-{high})"
        data = parse_json(result)
        return None, f"brent_price_not_extracted: {json.dumps(data)[:400]}" if data else "wealth_commodity_no_data"
    except Exception as e:
        return None, f"wealth_error: {e}"


def verify_gdp(prediction):
    """GDP requires web check — flag for agent follow-up."""
    return None, "requires_web_check: DOSM advance GDP Q3"


def verify_ron95_subsidy(prediction):
    """Budget speech requires web check — flag for agent follow-up."""
    return None, "requires_web_check: budget speech transcript"


# ── Event → verifier mapping ─────────────────────────────────────────

VERIFIERS = {
    "pred-5004749ee139": (verify_ron95_price, "fuel-price-window"),
    "pred-a1210fe9e53c": (verify_brent_range, "fuel-price-window"),
    "pred-6c2e5880d66b": (verify_gdp, "my-003-gdp-q3-2026"),
    "pred-fdb40342252b": (None, "budget-2027"),  # PETRONAS dividen — manual
    "pred-e3808eff19ee": (verify_ron95_subsidy, "budget-2027"),
}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    now = datetime.now(MYT)
    print(f"CHRON verify_due.py — {now.strftime('%Y-%m-%d %H:%M MYT')}")

    # 1. Get predictions due
    due_data = parse_json(chron_call("chron_predictions_due", {}))
    if not due_data or due_data.get("count", 0) == 0:
        next_at = due_data.get("next_verify_at", "unknown") if due_data else "unknown"
        log_entry({"event": "check", "due_count": 0, "next_verify": next_at})
        print(f"No predictions due. Next verify: {next_at}")
        return

    predictions = due_data.get("due", [])
    print(f"Found {len(predictions)} predictions due")

    verified = skipped = errors = 0

    for pred in predictions:
        pid = pred.get("prediction_id", "unknown")
        claim = pred.get("claim", "")[:80]
        verifier_entry = VERIFIERS.get(pid)
        verifier_fn = verifier_entry[0] if verifier_entry else None

        if verifier_fn is None:
            log_entry({"event": "skip", "prediction_id": pid, "reason": "no_automated_verifier", "claim": claim})
            print(f"  SKIP {pid}: no automated verifier — needs agent follow-up")
            skipped += 1
            continue

        correct, observed = verifier_fn(pred)

        if correct is None:
            log_entry({"event": "error", "prediction_id": pid, "observed": observed})
            print(f"  ERROR {pid}: {observed}")
            errors += 1
            continue

        # Record verification
        vr = chron_call("chron_record_verification", {
            "prediction_id": pid,
            "observed_outcome": observed,
            "correct": correct,
        })
        vr_data = parse_json(vr)

        log_entry({
            "event": "verified", "prediction_id": pid,
            "correct": correct, "observed": observed, "chron_response": vr_data,
        })
        print(f"  {'CORRECT' if correct else 'INCORRECT'} {pid}: {observed}")
        verified += 1

    # If no automated verifiers could fire, log for agent follow-up
    if verified == 0 and skipped > 0:
        log_entry({
            "event": "agent_followup_needed",
            "prediction_ids": [p.get("prediction_id") for p in predictions],
            "reason": "all_verifiers_missing_or_manual",
        })

    log_entry({"event": "run_complete", "verified": verified, "skipped": skipped, "errors": errors})
    print(f"\nDone: {verified} verified, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
