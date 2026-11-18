"""Integration and parity checks for CHRON MCP surface, clock, and FRAME witness bridge."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_server_module():
    import chron.server as srv

    return srv


def _load_annotations():
    spec = importlib.util.spec_from_file_location("mcp_annotations", _ROOT / "mcp_annotations.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_tool_count_and_annotations_parity():
    """All 24 FastMCP tools must have matching 1:1 entries in TOOL_ANNOTATIONS."""
    srv = _load_server_module()
    ann = _load_annotations()

    tools = asyncio.run(srv.mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert len(tool_names) == 24, f"expected 24 tools, got {len(tool_names)}: {sorted(tool_names)}"
    assert "chron_time_now" in tool_names
    assert "chron_frame_witness" in tool_names

    annotated = set(ann.TOOL_ANNOTATIONS.keys())
    assert annotated == tool_names, f"annotation mismatch diff={tool_names ^ annotated}"
    assert ann.TOOL_ANNOTATIONS["chron_time_now"]["readOnlyHint"] is True
    assert ann.TOOL_ANNOTATIONS["chron_time_now"]["destructiveHint"] is False


def test_chron_time_now_shape():
    """chron_time_now observes monotonic/wall clock without synthetic caching."""
    srv = _load_server_module()
    fn = getattr(srv.chron_time_now, "fn", srv.chron_time_now)
    out = fn()
    assert out["ttl_seconds"] == 0
    assert out["model_generated"] is False
    assert isinstance(out["monotonic_ns"], int) and out["monotonic_ns"] > 0
    assert out["observed_at_utc"].endswith("Z")
    assert "timezone" in out and out["utc_offset"]


def test_chron_frame_witness_roundtrip_mock(tmp_path, monkeypatch):
    """Hermetic mock HTTP integration test for chron_frame_witness witness + results actions."""
    srv = _load_server_module()
    import chron.chron_frame as cf

    monkeypatch.setattr(cf, "CHRON_DATA", tmp_path)

    responses = {
        "confirmed": {
            "witness": "claims",
            "claims_checked": 1,
            "counts": {"VERIFIED": 1, "CONTRADICTED": 0, "UNVERIFIABLE": 0, "STALE": 0, "EACCES": 0},
            "rows": [{"claim": "ok", "status": "VERIFIED"}],
            "authority": "OBSERVATIONAL_ONLY",
        },
        "contested": {
            "witness": "claims",
            "claims_checked": 1,
            "counts": {"VERIFIED": 0, "CONTRADICTED": 1, "UNVERIFIABLE": 0, "STALE": 0, "EACCES": 0},
            "rows": [{"claim": "bad claim", "status": "CONTRADICTED"}],
            "authority": "OBSERVATIONAL_ONLY",
        },
    }
    mode = {"key": "confirmed"}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/frame/witness/claims"
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            body = json.dumps(responses[mode["key"]]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setattr(srv, "FRAME_URL", f"http://127.0.0.1:{port}")
        fn = getattr(srv.chron_frame_witness, "fn", srv.chron_frame_witness)

        res_ok = fn(
            action="witness",
            claims=[{"text": "ok", "evidence": {"check": "file_exists", "path": "/var/lib/well/state.json"}}],
        )
        assert res_ok["witness_verdict"] == "CONFIRMED"
        assert res_ok["evidence_quality"] == "OBSERVED"
        cid = res_ok["claim_id"]

        mode["key"] = "contested"
        res_bad = fn(
            action="witness",
            claims=[{"text": "bad claim", "evidence": {"check": "file_exists", "path": "/nonexistent"}}],
        )
        assert res_bad["witness_verdict"] == "CONTESTED"
        assert "bad claim" in res_bad["contradictions"]

        hist = fn(action="results", claim_id=cid)
        assert hist["count"] == 1
        assert hist["witness_results"][0]["claim_id"] == cid

        monkeypatch.setattr(srv, "FRAME_URL", "http://127.0.0.1:9")
        unreachable = fn(
            action="witness",
            claims=[{"text": "offline", "evidence": {"check": "file_exists", "path": "/x"}}],
        )
        assert "error" in unreachable and unreachable["request_logged"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_live_chron_and_frame_endpoints():
    """Live integration probe against running CHRON (:18102) and FRAME (:18085) when enabled."""
    if os.environ.get("CHRON_RUN_LIVE_TESTS") != "1":
        pytest.skip("set CHRON_RUN_LIVE_TESTS=1 to run live endpoint integration tests")

    with urllib.request.urlopen("http://127.0.0.1:18102/health", timeout=5) as r:
        assert r.status == 200
        health = json.loads(r.read())
        assert health["organ"] == "CHRON"
        assert health["port"] == 18102

    req = urllib.request.Request(
        "http://127.0.0.1:18085/frame/witness/claims",
        data=json.dumps(
            {
                "claims": [
                    {
                        "text": "chron integration check",
                        "evidence": {"check": "file_exists", "path": "/root/AAA/FEDERATION.md"},
                    }
                ]
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        body = json.loads(r.read())
        assert body["witness"] == "claims"
        assert body["claims_checked"] == 1
