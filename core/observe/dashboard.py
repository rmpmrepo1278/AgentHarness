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
    from fastapi import FastAPI, Request
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

    # --- Mutation endpoints ---

    @app.post("/api/proposals/{proposal_id}/approve")
    async def api_approve(proposal_id: str):
        """Approve a proposal via the dashboard."""
        try:
            from core.approval.gateway import ApprovalGateway
            from core.approval.auth import validate_and_approve
            proposals_dir = str(data_path / "proposals")
            gw = ApprovalGateway(proposals_dir=proposals_dir)
            validate_and_approve(gw, proposal_id, source="dashboard")
            return JSONResponse({"status": "approved", "proposal_id": proposal_id})
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)

    @app.post("/api/proposals/{proposal_id}/reject")
    async def api_reject(proposal_id: str, request: Request):
        """Reject a proposal via the dashboard."""
        try:
            body = await request.json()
            reason = body.get("reason", "Rejected via dashboard")
            from core.approval.gateway import ApprovalGateway
            from core.approval.auth import validate_and_reject
            proposals_dir = str(data_path / "proposals")
            gw = ApprovalGateway(proposals_dir=proposals_dir)
            validate_and_reject(gw, proposal_id, reason=reason, source="dashboard")
            return JSONResponse({"status": "rejected", "proposal_id": proposal_id})
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)

    @app.post("/api/discover")
    async def api_discover():
        """Trigger a full discovery run."""
        try:
            from core.discovery.engine import run_discovery
            state = run_discovery()
            return JSONResponse({
                "status": "ok",
                "paths": len(state.get("paths", {})),
                "services": len(state.get("services", {}).get("docker_containers", [])),
            })
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

    @app.get("/api/validate")
    def api_validate():
        """Run pre-deploy validation."""
        try:
            from core.doctor.validate_remote import validate_local
            results = validate_local()
            passed = sum(1 for v in results.values() if v.get("status") == "ok")
            failed = sum(1 for v in results.values() if v.get("status") != "ok")
            return JSONResponse({"checks": results, "passed": passed, "failed": failed})
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!DOCTYPE html>
<html><head><title>AgentHarness</title>
<style>
body{font-family:monospace;max-width:900px;margin:40px auto;padding:0 20px;background:#1a1a2e;color:#e0e0e0}
h1{border-bottom:2px solid #0f3460;color:#e94560}
h2{color:#0f3460;margin-top:2em}
a{color:#e94560}
.section{background:#16213e;padding:15px;border-radius:8px;margin:10px 0}
button{background:#e94560;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-family:monospace}
button:hover{background:#c73350}
.status{display:inline-block;padding:2px 8px;border-radius:3px;font-size:0.9em}
.ok{background:#2d6a4f;color:white}
.fail{background:#e94560;color:white}
#output{background:#0f3460;padding:10px;border-radius:4px;margin-top:10px;white-space:pre-wrap;display:none;max-height:400px;overflow-y:auto}
</style></head>
<body>
<h1>AgentHarness Dashboard</h1>

<h2>Status</h2>
<div class="section">
<a href="/api/health">Health</a> |
<a href="/api/briefings">Briefings</a> |
<a href="/api/budget">LLM Budget</a> |
<a href="/api/proposals">Proposals</a> |
<a href="/api/validate">Validation</a>
</div>

<h2>Actions</h2>
<div class="section">
<button onclick="doAction('/api/discover', 'POST')">Run Discovery</button>
<button onclick="doAction('/api/validate', 'GET')">Run Validation</button>
</div>

<div id="output"></div>

<script>
async function doAction(url, method) {
    const out = document.getElementById('output');
    out.style.display = 'block';
    out.textContent = 'Running...';
    try {
        const resp = await fetch(url, {method});
        const data = await resp.json();
        out.textContent = JSON.stringify(data, null, 2);
    } catch(e) {
        out.textContent = 'Error: ' + e.message;
    }
}
</script>
</body></html>"""

    return app
