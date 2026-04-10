"""n8n Workflow MCP server. Trigger, list, and create n8n workflows."""
from __future__ import annotations
import os
import sys
import logging
import requests

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("n8n-mcp")

N8N_URL = os.environ.get("N8N_URL", "http://127.0.0.1:5678")

# Load API key from env file if not in environment
def _load_env():
    for path in ["/data/.env", os.path.expanduser("~/agentharness/data/.env")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and v and k not in os.environ:
                            os.environ[k] = v
_load_env()

N8N_API_KEY = os.environ.get("N8N_API_KEY", "")


def _n8n_headers():
    h = {"Content-Type": "application/json"}
    if N8N_API_KEY:
        h["X-N8N-API-KEY"] = N8N_API_KEY
    return h


def list_workflows(args: dict) -> dict:
    """List all n8n workflows."""
    try:
        resp = requests.get(f"{N8N_URL}/api/v1/workflows", headers=_n8n_headers(), timeout=10)
        resp.raise_for_status()
        workflows = resp.json().get("data", [])
        return {
            "workflows": [{"id": w["id"], "name": w["name"], "active": w["active"]} for w in workflows],
            "count": len(workflows),
        }
    except Exception as e:
        return {"error": str(e)}


def trigger_workflow(args: dict) -> dict:
    """Trigger an n8n workflow by ID or name."""
    workflow_id = args.get("id", "")
    name = args.get("name", "")
    payload = args.get("data", {})

    if not workflow_id and name:
        # Look up by name
        try:
            resp = requests.get(f"{N8N_URL}/api/v1/workflows", headers=_n8n_headers(), timeout=10)
            for w in resp.json().get("data", []):
                if name.lower() in w["name"].lower():
                    workflow_id = w["id"]
                    break
        except Exception:
            pass

    if not workflow_id:
        return {"error": "Workflow not found. Provide id or name."}

    try:
        # Try webhook trigger first (production URL)
        resp = requests.post(
            f"{N8N_URL}/webhook/{workflow_id}",
            json=payload,
            headers=_n8n_headers(),
            timeout=30,
        )
        if resp.status_code == 200:
            return {"status": "triggered", "workflow_id": workflow_id, "response": resp.json() if resp.text else {}}

        # Try test webhook
        resp = requests.post(
            f"{N8N_URL}/webhook-test/{workflow_id}",
            json=payload,
            headers=_n8n_headers(),
            timeout=30,
        )
        return {"status": "triggered", "workflow_id": workflow_id, "response": resp.json() if resp.text else {}}
    except Exception as e:
        return {"error": f"Failed to trigger workflow: {e}"}


def get_workflow(args: dict) -> dict:
    """Get details of a specific workflow."""
    workflow_id = args.get("id", "")
    if not workflow_id:
        return {"error": "workflow id required"}
    try:
        resp = requests.get(f"{N8N_URL}/api/v1/workflows/{workflow_id}", headers=_n8n_headers(), timeout=10)
        resp.raise_for_status()
        w = resp.json()
        return {
            "id": w.get("id"),
            "name": w.get("name"),
            "active": w.get("active"),
            "nodes": len(w.get("nodes", [])),
            "created": w.get("createdAt"),
            "updated": w.get("updatedAt"),
        }
    except Exception as e:
        return {"error": str(e)}


def toggle_workflow(args: dict) -> dict:
    """Activate or deactivate a workflow."""
    workflow_id = args.get("id", "")
    active = args.get("active", True)
    if not workflow_id:
        return {"error": "workflow id required"}
    try:
        resp = requests.patch(
            f"{N8N_URL}/api/v1/workflows/{workflow_id}",
            json={"active": active},
            headers=_n8n_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return {"status": "activated" if active else "deactivated", "workflow_id": workflow_id}
    except Exception as e:
        return {"error": str(e)}


def list_executions(args: dict) -> dict:
    """List recent workflow executions."""
    limit = args.get("limit", 10)
    try:
        resp = requests.get(
            f"{N8N_URL}/api/v1/executions",
            params={"limit": limit},
            headers=_n8n_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        execs = resp.json().get("data", [])
        return {
            "executions": [{
                "id": e.get("id"),
                "workflow": e.get("workflowData", {}).get("name", "?"),
                "status": e.get("status", "?"),
                "started": e.get("startedAt"),
                "finished": e.get("stoppedAt"),
            } for e in execs],
            "count": len(execs),
        }
    except Exception as e:
        return {"error": str(e)}


TOOL_SCHEMAS = [
    {
        "name": "list_workflows",
        "description": "List all n8n automation workflows.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "trigger_workflow",
        "description": "Trigger an n8n workflow by ID or name. Optionally pass data payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Workflow ID"},
                "name": {"type": "string", "description": "Workflow name (partial match)"},
                "data": {"type": "object", "description": "Optional data payload for the workflow"},
            },
        },
    },
    {
        "name": "get_workflow",
        "description": "Get details of a specific n8n workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Workflow ID"}},
            "required": ["id"],
        },
    },
    {
        "name": "toggle_workflow",
        "description": "Activate or deactivate an n8n workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Workflow ID"},
                "active": {"type": "boolean", "description": "true to activate, false to deactivate"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_executions",
        "description": "List recent n8n workflow execution history.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Number of recent executions (default: 10)"}},
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8098"))
    server = MCPServer(name="n8n", port=port, tools=TOOL_SCHEMAS)

    server.register_handler("list_workflows", list_workflows)
    server.register_handler("trigger_workflow", trigger_workflow)
    server.register_handler("get_workflow", get_workflow)
    server.register_handler("toggle_workflow", toggle_workflow)
    server.register_handler("list_executions", list_executions)

    log.info(f"n8n MCP starting on :{port} with {len(TOOL_SCHEMAS)} tools")
    server.start()


if __name__ == "__main__":
    main()
