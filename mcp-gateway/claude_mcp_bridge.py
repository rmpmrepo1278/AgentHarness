#!/usr/bin/env python3
"""MCP stdio bridge for Claude Code.

Translates JSON-RPC 2.0 over stdin/stdout (MCP protocol) into HTTP calls
against the AgentHarness MCP gateway REST API.

Usage:
    python3 claude_mcp_bridge.py

Environment:
    MCP_GATEWAY_URL  –  gateway base URL (default http://192.168.29.10:8096)
"""

import json
import os
import sys
import urllib.request
import urllib.error

GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://192.168.29.10:8090").rstrip("/")

SERVER_INFO = {
    "name": "agentharness-mcp-bridge",
    "version": "1.0.0",
}

# ---------------------------------------------------------------------------
# Logging (stderr only – stdout is reserved for JSON-RPC)
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[mcp-bridge] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _http_get(path: str, timeout: int = 15):
    url = f"{GATEWAY_URL}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError) as exc:
        log(f"GET {url} failed: {exc}")
        return None


def _http_post(path: str, body: dict, timeout: int = 30):
    url = f"{GATEWAY_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError) as exc:
        log(f"POST {url} failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

def fetch_tools() -> list:
    """Fetch tools from gateway and convert to MCP format."""
    catalog = _http_get("/tools/catalog")
    if not catalog:
        log("WARNING: could not fetch tool catalog – returning empty list")
        return []

    raw_tools = catalog.get("tools", [])
    mcp_tools = []
    for t in raw_tools:
        # Gateway returns OpenAI function-calling format:
        #   {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        # or flat format: {"name": ..., "description": ..., "parameters": {...}}
        func = t.get("function", t)
        name = func.get("name", "")
        description = func.get("description", "")
        parameters = func.get("parameters", {"type": "object", "properties": {}})

        mcp_tools.append({
            "name": name,
            "description": description,
            "inputSchema": parameters,
        })

    log(f"Loaded {len(mcp_tools)} tools from gateway")
    return mcp_tools


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def send(obj: dict) -> None:
    line = json.dumps(obj, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def reply(req_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def error(req_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

def handle_initialize(req_id, _params: dict, _tools: list) -> None:
    reply(req_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
        },
        "serverInfo": SERVER_INFO,
    })


def handle_tools_list(req_id, _params: dict, tools: list) -> None:
    reply(req_id, {"tools": tools})


def handle_tools_call(req_id, params: dict, _tools: list) -> None:
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    resp = _http_post("/tools/call", {"name": name, "arguments": arguments})

    if resp is None:
        error(req_id, -32000, f"Gateway unreachable when calling tool '{name}'")
        return

    # If gateway returned an error key, surface it
    if "error" in resp:
        reply(req_id, {
            "content": [{"type": "text", "text": json.dumps(resp["error"])}],
            "isError": True,
        })
        return

    # Wrap the result in MCP content format
    # The gateway may return arbitrary JSON; serialize it as text for Claude
    result_text = json.dumps(resp.get("result", resp), indent=2)
    reply(req_id, {
        "content": [{"type": "text", "text": result_text}],
    })


HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log(f"Starting – gateway: {GATEWAY_URL}")

    tools = fetch_tools()

    log("Listening on stdin for JSON-RPC messages...")

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log(f"Bad JSON: {exc}")
            continue

        method = msg.get("method", "")
        req_id = msg.get("id")  # None for notifications
        params = msg.get("params", {})

        # Notifications (no id) – just acknowledge silently
        if req_id is None:
            if method == "notifications/initialized":
                log("Client initialized notification received")
            else:
                log(f"Notification: {method}")
            continue

        handler = HANDLERS.get(method)
        if handler:
            try:
                handler(req_id, params, tools)
            except Exception as exc:
                log(f"Handler error for {method}: {exc}")
                error(req_id, -32603, str(exc))
        else:
            log(f"Unknown method: {method}")
            error(req_id, -32601, f"Method not found: {method}")

    log("stdin closed – exiting")


if __name__ == "__main__":
    main()
