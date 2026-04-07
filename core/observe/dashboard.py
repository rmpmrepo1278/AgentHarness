"""Optional FastAPI web dashboard for AgentHarness observability.

Single file. Server-rendered HTML. No JS framework. LAN-only by default.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def create_app(data_dir: str, auth_token: str = "") -> Any:
    """Create the FastAPI dashboard app."""
    if not HAS_FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn")

    app = FastAPI(title="AgentHarness Dashboard")
    data_path = Path(data_dir)

    @app.get("/api/health")
    def api_health():
        heartbeat_file = data_path / "heartbeat.json"
        if heartbeat_file.exists():
            try:
                hb = json.loads(heartbeat_file.read_text())
                return JSONResponse({"status": "ok", "heartbeat": hb})
            except Exception:
                pass
        return JSONResponse({"status": "unknown"})

    @app.get("/api/briefings")
    def api_briefings():
        briefings_dir = data_path / "briefings"
        briefings = []
        if briefings_dir.is_dir():
            for f in sorted(briefings_dir.glob("*.json"), reverse=True)[:7]:
                try:
                    briefings.append(json.loads(f.read_text()))
                except Exception:
                    continue
        return JSONResponse(briefings)

    @app.get("/api/budget")
    def api_budget():
        budget_file = data_path / "llm_budget.json"
        if budget_file.exists():
            try:
                return JSONResponse(json.loads(budget_file.read_text()))
            except Exception:
                pass
        return JSONResponse({})

    @app.get("/api/proposals")
    def api_proposals():
        proposals_dir = data_path / "proposals"
        proposals = []
        if proposals_dir.is_dir():
            for f in sorted(proposals_dir.glob("*.json"), reverse=True)[:20]:
                try:
                    proposals.append(json.loads(f.read_text()))
                except Exception:
                    continue
        return JSONResponse(proposals)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!DOCTYPE html>
<html><head><title>AgentHarness</title>
<style>body{font-family:monospace;max-width:800px;margin:40px auto;padding:0 20px}
h1{border-bottom:2px solid #333}a{color:#0066cc}</style></head>
<body><h1>AgentHarness Dashboard</h1>
<ul>
<li><a href="/api/health">Health</a></li>
<li><a href="/api/briefings">Briefings</a></li>
<li><a href="/api/budget">LLM Budget</a></li>
<li><a href="/api/proposals">Proposals</a></li>
</ul></body></html>"""

    return app
