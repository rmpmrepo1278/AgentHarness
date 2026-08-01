"""Doctor MCP server. Provides health-check and runbook tools via JSON-RPC."""
from __future__ import annotations
import os, sys, logging, json
import requests

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("doctor-mcp")

DOCTOR_BASE = os.environ.get("DOCTOR_BASE_URL", "http://127.0.0.1:8080/doctor")

_OK_STATES = {"ok", "healthy", "running", "pass"}


def _status_label(st):
    return "OK" if st in _OK_STATES else "FAIL"


def doctor_status(args: dict) -> dict:
    """Get overall health status of the homelab."""
    try:
        r = requests.get(f"{DOCTOR_BASE}/status", timeout=30)
        r.raise_for_status()
        data = r.json()
        lines = []
        overall = data.get("status", data.get("overall", "unknown"))
        lines.append(f"Overall: {overall}")
        checks = data.get("checks", data.get("services", []))
        if isinstance(checks, list):
            for chk in checks:
                if isinstance(chk, dict):
                    name = chk.get("name", chk.get("service", "?"))
                    st = chk.get("status", "?")
                    label = _status_label(st)
                    lines.append(f"  {label} {name}: {st}")
        elif isinstance(checks, dict):
            for name, st in checks.items():
                sv = st if isinstance(st, str) else st.get("status", "?")
                label = _status_label(sv)
                lines.append(f"  {label} {name}: {sv}")
        return {"summary": "\n".join(lines), "raw": data}
    except requests.RequestException as e:
        return {"error": f"Doctor API unreachable: {e}"}


def doctor_runbooks(args: dict) -> dict:
    """List available doctor runbooks."""
    try:
        r = requests.get(f"{DOCTOR_BASE}/runbooks", timeout=15)
        r.raise_for_status()
        data = r.json()
        runbooks = data if isinstance(data, list) else data.get("runbooks", [])
        return {"runbooks": runbooks}
    except requests.RequestException as e:
        return {"error": f"Doctor API unreachable: {e}"}


def doctor_heal(args: dict) -> dict:
    """Run a doctor runbook to fix a specific issue."""
    name = args.get("name", "")
    if not name:
        return {"error": "runbook name required"}
    try:
        r = requests.post(f"{DOCTOR_BASE}/run/{name}", timeout=120)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"error": f"Runbook execution failed: {e}"}


TOOL_SCHEMAS = [
    {
        "name": "doctor_status",
        "description": "Get homelab health status. Returns overall status and per-service checks.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "doctor_runbooks",
        "description": "List available doctor runbooks (auto-fix scripts for common issues).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "doctor_heal",
        "description": "Run a doctor runbook by name to auto-fix a specific issue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Runbook name to execute"},
            },
            "required": ["name"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8105"))
    s = MCPServer(name="doctor", port=port, tools=TOOL_SCHEMAS)
    s.register_handler("doctor_status", doctor_status)
    s.register_handler("doctor_runbooks", doctor_runbooks)
    s.register_handler("doctor_heal", doctor_heal)
    log.info(f"Doctor MCP starting on :{port}")
    s.start()


if __name__ == "__main__":
    main()
