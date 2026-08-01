#!/usr/bin/env python3
"""MCP Gateway - Central registry and router for all MCP services."""
from __future__ import annotations
import logging
import os
import time
import json
import threading

from flask import Flask, request, jsonify

import registry
import router
import health
import gateway_log

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gateway")

app = Flask(__name__)

_start_time = time.time()


@app.route("/register", methods=["POST"])
def register_mcp():
    data = request.json or {}
    name = data.get("name")
    address = data.get("address")
    if not name or not address:
        return jsonify({"error": "name and address required"}), 400

    container_name = data.get("container_name", name)
    tools = data.get("tools")

    if not tools:
        try:
            resp = http_requests.post(
                address,
                json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
                timeout=120,
            )
            tools = resp.json().get("result", {}).get("tools", [])
        except Exception as e:
            log.warning(f"Could not fetch tools from {name}: {e}")
            tools = []

    mcp = registry.register(name, address, container_name=container_name, tools=tools)
    return jsonify({"status": "registered", "mcp": name, "tools": len(mcp["tools"])})


@app.route("/deregister", methods=["POST"])
def deregister_mcp():
    data = request.json or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "name required"}), 400
    removed = registry.deregister(name)
    return jsonify({"status": "deregistered" if removed else "not_found", "mcp": name})


@app.route("/tools/catalog", methods=["GET"])
def tools_catalog():
    catalog = router.get_catalog()
    return jsonify({"tools": catalog, "count": len(catalog)})


@app.route("/tools/call", methods=["POST"])
def tools_call():
    data = request.json or {}
    tool_name = data.get("name")
    arguments = data.get("arguments", {})
    if not tool_name:
        return jsonify({"error": "name required"}), 400
    result = router.call_tool(tool_name, arguments)
    status_code = 200 if "error" not in result else (429 if result.get("error") == "rate_limited" else 502)
    return jsonify(result), status_code


@app.route("/status", methods=["GET"])
def status():
    mcps = registry.get_all()
    return jsonify({
        "status": "running",
        "uptime_seconds": int(time.time() - _start_time),
        "mcps_registered": len(mcps),
        "mcps_healthy": sum(1 for m in mcps.values() if m["status"] == "healthy"),
        "mcps_degraded": sum(1 for m in mcps.values() if m["status"] == "degraded"),
        "mcps_offline": sum(1 for m in mcps.values() if m["status"] in ("offline", "failed")),
        "total_tools": sum(len(m.get("tools", [])) for m in mcps.values()),
    })


@app.route("/mcps", methods=["GET"])
def list_mcps():
    mcps = registry.get_all()
    summary = {}
    for name, mcp in mcps.items():
        summary[name] = {
            "status": mcp["status"],
            "address": mcp["address"],
            "tools": len(mcp.get("tools", [])),
            "last_seen": mcp.get("last_seen"),
        }
    return jsonify({"mcps": summary, "total": len(summary)})


if __name__ == "__main__":
    import http_requests
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("gateway")

    port = int(os.environ.get("GATEWAY_PORT", "8090"))
    app.run(host="0.0.0.0", port=port)