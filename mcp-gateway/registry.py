"""MCP server registry with persistence and state management."""
from __future__ import annotations
import json
import os
import threading
import logging
from datetime import datetime, timezone

import gateway_log

log = logging.getLogger("registry")

_STATE_FILE = os.environ.get("GATEWAY_STATE_FILE", "/data/gateway_state.json")
_lock = threading.Lock()
_mcps: dict[str, dict] = {}


def _save():
    """Persist registry state to disk."""
    os.makedirs(os.path.dirname(_STATE_FILE) or ".", exist_ok=True)
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_mcps, f, indent=2)
    os.replace(tmp, _STATE_FILE)


def _load():
    """Load persisted registry state."""
    global _mcps
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE) as f:
                _mcps = json.load(f)
            log.info(f"Loaded {len(_mcps)} MCPs from state file")
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load state file: {e}")
            _mcps = {}


def init():
    """Initialize registry from persisted state. Call on startup."""
    with _lock:
        _load()
        for name, mcp in _mcps.items():
            mcp["status"] = "unknown"
            mcp["consecutive_failures"] = 0
        _save()


def register(name: str, address: str, container_name: str = None, tools: list = None) -> dict:
    """Register or re-register an MCP server."""
    with _lock:
        now = datetime.now(timezone.utc).isoformat()
        existing = _mcps.get(name)
        _mcps[name] = {
            "name": name,
            "address": address,
            "container_name": container_name or name,
            "status": "healthy",
            "registered_at": existing["registered_at"] if existing else now,
            "last_registered": now,
            "last_health_check": now,
            "consecutive_failures": 0,
            "tools": tools or (existing["tools"] if existing else []),
        }
        _save()

    gateway_log.emit("mcp_registered", mcp=name, address=address,
                     tools=len(_mcps[name]["tools"]))
    log.info(f"Registered MCP: {name} at {address} ({len(_mcps[name]['tools'])} tools)")
    return _mcps[name]


def deregister(name: str) -> bool:
    """Remove an MCP from the registry."""
    with _lock:
        if name in _mcps:
            del _mcps[name]
            _save()
            gateway_log.emit("mcp_deregistered", mcp=name)
            log.info(f"Deregistered MCP: {name}")
            return True
    return False


def get(name: str) -> dict | None:
    return _mcps.get(name)


def get_all() -> dict:
    return dict(_mcps)


def update_status(name: str, status: str):
    with _lock:
        if name in _mcps:
            old_status = _mcps[name]["status"]
            _mcps[name]["status"] = status
            _mcps[name]["last_health_check"] = datetime.now(timezone.utc).isoformat()
            if status != old_status:
                gateway_log.emit(f"health_{status}", mcp=name,
                                 consecutive_failures=_mcps[name]["consecutive_failures"])
            _save()


def record_health_success(name: str):
    with _lock:
        if name in _mcps:
            _mcps[name]["consecutive_failures"] = 0
            _mcps[name]["status"] = "healthy"
            _mcps[name]["last_health_check"] = datetime.now(timezone.utc).isoformat()
            _save()


def record_health_failure(name: str) -> int:
    with _lock:
        if name in _mcps:
            _mcps[name]["consecutive_failures"] += 1
            _mcps[name]["last_health_check"] = datetime.now(timezone.utc).isoformat()
            count = _mcps[name]["consecutive_failures"]
            _save()
            return count
    return 0


def update_tools(name: str, tools: list):
    with _lock:
        if name in _mcps:
            _mcps[name]["tools"] = tools
            _save()


def get_healthy_tools() -> list[tuple[str, dict]]:
    """Get all tools from healthy/degraded MCPs. Returns [(mcp_name, tool_schema), ...]."""
    result = []
    for name, mcp in _mcps.items():
        if mcp["status"] in ("healthy", "degraded"):
            for tool in mcp.get("tools", []):
                result.append((name, tool))
    return result
