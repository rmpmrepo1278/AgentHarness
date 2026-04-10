# MCP Gateway + Docker MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent MCP gateway that auto-discovers MCP servers and a Docker MCP that gives Chaguli container management — all with zero manual steps after initial deploy.

**Architecture:** Flask-based gateway (registry + health monitor + tool router) communicates with MCP servers via JSON-RPC. Docker MCP is the first server, providing 6 container tools. MCP servers self-register with retry-backoff. Gateway auto-restarts failed MCPs via Docker socket. Chaguli bridges to the gateway via a thin dispatch patch.

**Tech Stack:** Python 3.11, Flask, Docker SDK for Python, JSON-RPC 2.0, inotify-simple, PyYAML

---

## File Structure

```
AgentHarness/
  mcp-gateway/
    Dockerfile                  # Python 3.11-slim, flask + docker SDK
    requirements.txt            # flask, docker, requests
    server.py                   # Flask HTTP API (register, deregister, tools/catalog, tools/call, status, mcps, logs)
    registry.py                 # MCP registry: in-memory + persisted to gateway_state.json
    health.py                   # Health monitor thread: adaptive frequency, auto-restart, escalation
    router.py                   # Tool name → MCP routing, OpenAI format conversion
    rate_limiter.py             # Per-tool + global rate limiting
    gateway_log.py              # Structured JSON-line logger
    notify.py                   # Write alerts to Chaguli's alerts_inbox.jsonl
    mcp_base.py                 # Base class for MCP servers (registration retry, health endpoint)
    data/                       # Mounted volume: gateway_state.json, gateway.log
  docker-mcp/
    Dockerfile                  # Python 3.11-slim, docker SDK + pyyaml
    requirements.txt            # docker, pyyaml, inotify-simple, requests
    server.py                   # JSON-RPC MCP server, extends mcp_base
    tools.py                    # 6 Docker tool implementations
    templates.py                # Template resolution: repo → local override, hot-reload
    port_allocator.py           # Find next free host port
    resource_guard.py           # Memory/disk pre-deploy checks
    secrets.py                  # Auto-generate + persist secrets for templates
  templates/
    docker/
      paperless-ngx.yml         # Vetted compose template
      uptime-kuma.yml           # Vetted compose template
  docker-compose.mcp.yml        # Compose file for gateway + docker-mcp
  scripts/
    setup_mcp_gateway.sh        # One-command bootstrap
```

---

### Task 1: MCP Gateway — Structured Logger

**Files:**
- Create: `mcp-gateway/gateway_log.py`
- Test: Manual — verify JSON lines written to file

This is a dependency for every other component, so build it first.

- [ ] **Step 1: Create gateway_log.py**

```python
"""Structured JSON-line logger for the MCP gateway."""
import json
import os
import time
import threading
import logging
from datetime import datetime, timezone

log = logging.getLogger("gateway_log")

_LOG_FILE = os.environ.get("GATEWAY_LOG_FILE", "/data/gateway.log")
_MAX_SIZE_MB = int(os.environ.get("GATEWAY_LOG_MAX_MB", "50"))
_RETENTION_DAYS = int(os.environ.get("GATEWAY_LOG_RETENTION_DAYS", "7"))
_lock = threading.Lock()


def _rotate_if_needed():
    """Rotate log file if it exceeds max size."""
    try:
        if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > _MAX_SIZE_MB * 1024 * 1024:
            rotated = f"{_LOG_FILE}.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(_LOG_FILE, rotated)
            # Clean old rotated files
            log_dir = os.path.dirname(_LOG_FILE) or "."
            cutoff = time.time() - (_RETENTION_DAYS * 86400)
            for f in os.listdir(log_dir):
                fp = os.path.join(log_dir, f)
                if f.startswith(os.path.basename(_LOG_FILE) + ".") and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
    except OSError:
        pass


def emit(event: str, **kwargs):
    """Write a structured log event."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    with _lock:
        _rotate_if_needed()
        os.makedirs(os.path.dirname(_LOG_FILE) or ".", exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")


def recent(limit: int = 50, event_filter: str = None) -> list:
    """Read recent log entries, optionally filtered by event type."""
    if not os.path.exists(_LOG_FILE):
        return []
    entries = []
    try:
        with open(_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if event_filter and entry.get("event") != event_filter:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries[-limit:]
```

- [ ] **Step 2: Verify it works**

```bash
cd mcp-gateway && python3 -c "
import gateway_log
gateway_log._LOG_FILE = '/tmp/test_gateway.log'
gateway_log.emit('test_event', mcp='docker', tools=6)
gateway_log.emit('tool_call', tool='list_containers', success=True)
print(gateway_log.recent(limit=5))
"
```

Expected: Two JSON entries printed.

- [ ] **Step 3: Commit**

```bash
git add mcp-gateway/gateway_log.py
git commit -m "feat(mcp-gateway): add structured JSON-line logger"
```

---

### Task 2: MCP Gateway — Rate Limiter

**Files:**
- Create: `mcp-gateway/rate_limiter.py`

- [ ] **Step 1: Create rate_limiter.py**

```python
"""Per-tool and global rate limiting for the MCP gateway."""
import time
import threading
from collections import defaultdict

_DEFAULT_LIMITS = {
    "list_containers": 10,
    "container_status": 10,
    "container_logs": 10,
    "deploy_stack": 2,
    "remove_container": 3,
    "restart_container": 3,
}
_GLOBAL_LIMIT = 30  # max calls/min across all tools
_WINDOW_SECONDS = 60

_lock = threading.Lock()
_calls: dict[str, list[float]] = defaultdict(list)  # tool_name -> [timestamps]
_global_calls: list[float] = []


def _prune(timestamps: list[float]) -> list[float]:
    cutoff = time.time() - _WINDOW_SECONDS
    return [t for t in timestamps if t > cutoff]


def check(tool_name: str) -> tuple[bool, int]:
    """Check if a tool call is allowed.
    Returns (allowed: bool, retry_after_seconds: int)."""
    now = time.time()
    with _lock:
        # Prune old entries
        _calls[tool_name] = _prune(_calls[tool_name])
        global _global_calls
        _global_calls = _prune(_global_calls)

        # Check per-tool limit
        limit = _DEFAULT_LIMITS.get(tool_name, 10)
        if len(_calls[tool_name]) >= limit:
            oldest = _calls[tool_name][0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(retry_after, 1)

        # Check global limit
        if len(_global_calls) >= _GLOBAL_LIMIT:
            oldest = _global_calls[0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(retry_after, 1)

        # Allowed — record the call
        _calls[tool_name].append(now)
        _global_calls.append(now)
        return True, 0


def set_limit(tool_name: str, max_per_minute: int):
    """Override the default limit for a tool."""
    _DEFAULT_LIMITS[tool_name] = max_per_minute
```

- [ ] **Step 2: Quick verification**

```bash
cd mcp-gateway && python3 -c "
import rate_limiter
for i in range(5):
    ok, retry = rate_limiter.check('deploy_stack')
    print(f'Call {i+1}: allowed={ok}, retry_after={retry}')
"
```

Expected: First 2 allowed, calls 3-5 rejected with retry_after > 0.

- [ ] **Step 3: Commit**

```bash
git add mcp-gateway/rate_limiter.py
git commit -m "feat(mcp-gateway): add per-tool and global rate limiter"
```

---

### Task 3: MCP Gateway — Registry

**Files:**
- Create: `mcp-gateway/registry.py`

- [ ] **Step 1: Create registry.py**

```python
"""MCP server registry with persistence and state management."""
import json
import os
import time
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
        # Mark all loaded MCPs as 'unknown' until re-probed
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
    """Get a single MCP entry."""
    return _mcps.get(name)


def get_all() -> dict:
    """Get all registered MCPs."""
    return dict(_mcps)


def update_status(name: str, status: str):
    """Update an MCP's status."""
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
    """Record a successful health check."""
    with _lock:
        if name in _mcps:
            _mcps[name]["consecutive_failures"] = 0
            _mcps[name]["status"] = "healthy"
            _mcps[name]["last_health_check"] = datetime.now(timezone.utc).isoformat()
            _save()


def record_health_failure(name: str) -> int:
    """Record a failed health check. Returns new consecutive failure count."""
    with _lock:
        if name in _mcps:
            _mcps[name]["consecutive_failures"] += 1
            _mcps[name]["last_health_check"] = datetime.now(timezone.utc).isoformat()
            count = _mcps[name]["consecutive_failures"]
            _save()
            return count
    return 0


def update_tools(name: str, tools: list):
    """Update the cached tool catalog for an MCP."""
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
```

- [ ] **Step 2: Verify**

```bash
cd mcp-gateway && python3 -c "
import os; os.environ['GATEWAY_STATE_FILE'] = '/tmp/test_state.json'
os.environ['GATEWAY_LOG_FILE'] = '/tmp/test_gw.log'
import registry
registry.init()
registry.register('docker', 'http://docker-mcp:8095', container_name='docker-mcp', tools=[{'name':'list_containers'}])
print(registry.get_all())
print(registry.get_healthy_tools())
registry.deregister('docker')
print(registry.get_all())
"
```

Expected: Shows registered MCP, tools, then empty after deregister.

- [ ] **Step 3: Commit**

```bash
git add mcp-gateway/registry.py
git commit -m "feat(mcp-gateway): add MCP registry with persistence"
```

---

### Task 4: MCP Gateway — Notification Bridge

**Files:**
- Create: `mcp-gateway/notify.py`

- [ ] **Step 1: Create notify.py**

```python
"""Send notifications to Chaguli via alerts_inbox.jsonl."""
import json
import os
import time
import logging

log = logging.getLogger("notify")

_ALERTS_DIR = os.environ.get("CHAGULI_ALERTS_DIR", "/data/alerts")


def send_alert(title: str, message: str, severity: str = "warning"):
    """Write an alert to Chaguli's alerts inbox."""
    os.makedirs(_ALERTS_DIR, exist_ok=True)
    alert_file = os.path.join(_ALERTS_DIR, "alerts_inbox.jsonl")
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "mcp-gateway",
        "severity": severity,
        "title": title,
        "message": message,
        "delivered": False,
    }
    try:
        with open(alert_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        log.info(f"Alert sent: [{severity}] {title}")
    except OSError as e:
        log.error(f"Failed to write alert: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add mcp-gateway/notify.py
git commit -m "feat(mcp-gateway): add notification bridge to Chaguli"
```

---

### Task 5: MCP Gateway — Health Monitor

**Files:**
- Create: `mcp-gateway/health.py`

- [ ] **Step 1: Create health.py**

```python
"""Health monitor for registered MCP servers.
Adaptive frequency, auto-restart via Docker, escalation to user."""
import time
import threading
import logging
import requests

import docker

import registry
import gateway_log
import notify

log = logging.getLogger("health")

# Adaptive intervals (seconds)
_INTERVAL_HEALTHY = 60
_INTERVAL_DEGRADED = 15
_INTERVAL_POST_RESTART = 10
_INTERVAL_FAILED = 300

_DEGRADED_THRESHOLD = 3
_OFFLINE_THRESHOLD = 5

_docker_client = None
_stop_event = threading.Event()


def _get_docker():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def _check_one(name: str, mcp: dict) -> bool:
    """Ping one MCP. Returns True if healthy."""
    try:
        resp = requests.get(f"{mcp['address']}/health", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _restart_container(container_name: str) -> bool:
    """Restart a Docker container. Returns True on success."""
    try:
        client = _get_docker()
        container = client.containers.get(container_name)
        container.restart(timeout=30)
        gateway_log.emit("auto_restart", container=container_name)
        log.info(f"Restarted container: {container_name}")
        return True
    except Exception as e:
        log.error(f"Failed to restart {container_name}: {e}")
        return False


def _get_interval(status: str) -> float:
    """Get health check interval based on MCP status."""
    return {
        "healthy": _INTERVAL_HEALTHY,
        "degraded": _INTERVAL_DEGRADED,
        "offline": _INTERVAL_POST_RESTART,
        "failed": _INTERVAL_FAILED,
        "unknown": 5,  # fast probe on startup
    }.get(status, _INTERVAL_HEALTHY)


def check_all():
    """Run one health check cycle for all registered MCPs."""
    for name, mcp in registry.get_all().items():
        healthy = _check_one(name, mcp)

        if healthy:
            old_status = mcp["status"]
            registry.record_health_success(name)
            if old_status in ("degraded", "offline", "unknown"):
                gateway_log.emit("mcp_recovered", mcp=name, previous_status=old_status)
                log.info(f"MCP recovered: {name} ({old_status} -> healthy)")
                # Refresh tool catalog on recovery
                try:
                    resp = requests.post(
                        f"{mcp['address']}",
                        json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
                        timeout=10,
                    )
                    tools = resp.json().get("result", {}).get("tools", [])
                    if tools:
                        registry.update_tools(name, tools)
                        gateway_log.emit("catalog_refresh", mcp=name, tools=len(tools))
                except Exception as e:
                    log.warning(f"Failed to refresh tools for {name}: {e}")
        else:
            failures = registry.record_health_failure(name)

            if failures == _DEGRADED_THRESHOLD:
                registry.update_status(name, "degraded")
                log.warning(f"MCP degraded: {name} ({failures} failures)")

            elif failures == _OFFLINE_THRESHOLD:
                registry.update_status(name, "offline")
                log.error(f"MCP offline: {name} — attempting restart")
                container_name = mcp.get("container_name", name)
                restarted = _restart_container(container_name)
                if not restarted:
                    registry.update_status(name, "failed")
                    notify.send_alert(
                        f"MCP {name} failed",
                        f"MCP server '{name}' is offline and restart failed. Needs manual attention.",
                        severity="error",
                    )

            elif failures > _OFFLINE_THRESHOLD + 12:
                # 12 more checks after restart attempt (~2 min at 10s interval) — give up
                if mcp.get("status") != "failed":
                    registry.update_status(name, "failed")
                    notify.send_alert(
                        f"MCP {name} failed to recover",
                        f"MCP server '{name}' did not recover after automatic restart. Tools are disabled.",
                        severity="error",
                    )


def run(stop_event: threading.Event = None):
    """Run the health monitor loop."""
    stop = stop_event or _stop_event
    gateway_log.emit("health_monitor_started")
    log.info("Health monitor started")

    while not stop.is_set():
        check_all()

        # Sleep for the shortest interval needed
        mcps = registry.get_all()
        if mcps:
            interval = min(_get_interval(m["status"]) for m in mcps.values())
        else:
            interval = _INTERVAL_HEALTHY
        stop.wait(interval)


def start() -> threading.Thread:
    """Start the health monitor in a background thread."""
    t = threading.Thread(target=run, daemon=True, name="health-monitor")
    t.start()
    return t


def stop():
    """Stop the health monitor."""
    _stop_event.set()
```

- [ ] **Step 2: Commit**

```bash
git add mcp-gateway/health.py
git commit -m "feat(mcp-gateway): add health monitor with adaptive frequency and auto-restart"
```

---

### Task 6: MCP Gateway — Tool Router

**Files:**
- Create: `mcp-gateway/router.py`

- [ ] **Step 1: Create router.py**

```python
"""Route tool calls to the correct MCP server.
Converts MCP tool schemas to OpenAI function-calling format."""
import logging
import requests
import time

import registry
import rate_limiter
import gateway_log

log = logging.getLogger("router")


def _mcp_to_openai_tool(mcp_tool: dict) -> dict:
    """Convert an MCP tool schema to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.get("name", ""),
            "description": mcp_tool.get("description", ""),
            "parameters": mcp_tool.get("inputSchema", mcp_tool.get("parameters", {
                "type": "object",
                "properties": {},
            })),
        },
    }


def get_catalog() -> list[dict]:
    """Get all available tools in OpenAI function-calling format."""
    tools = []
    for mcp_name, tool_schema in registry.get_healthy_tools():
        openai_tool = _mcp_to_openai_tool(tool_schema)
        # Tag with source MCP for routing
        openai_tool["_mcp_source"] = mcp_name
        tools.append(openai_tool)
    return tools


def _find_owner(tool_name: str) -> tuple[str, dict] | tuple[None, None]:
    """Find which MCP owns a tool. Returns (mcp_name, mcp_entry) or (None, None)."""
    for name, mcp in registry.get_all().items():
        if mcp["status"] in ("healthy", "degraded"):
            for tool in mcp.get("tools", []):
                if tool.get("name") == tool_name:
                    return name, mcp
    return None, None


def call_tool(tool_name: str, arguments: dict) -> dict:
    """Route a tool call to the correct MCP server.

    Returns:
        {"result": ..., "mcp": "...", "duration_ms": N}
        or {"error": "...", "retry_after": N} on failure
    """
    # Rate limit check
    allowed, retry_after = rate_limiter.check(tool_name)
    if not allowed:
        gateway_log.emit("rate_limited", tool=tool_name, retry_after=retry_after)
        return {"error": "rate_limited", "retry_after_seconds": retry_after}

    # Find owner
    mcp_name, mcp = _find_owner(tool_name)
    if mcp is None:
        return {"error": f"No MCP server provides tool '{tool_name}'"}

    if mcp["status"] == "degraded":
        log.warning(f"Routing {tool_name} to degraded MCP {mcp_name}")

    # Call the MCP via JSON-RPC
    start = time.time()
    try:
        resp = requests.post(
            mcp["address"],
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": 1,
            },
            timeout=120,  # deploys can take a while
        )
        resp.raise_for_status()
        body = resp.json()
        duration_ms = int((time.time() - start) * 1000)

        if "error" in body:
            gateway_log.emit("tool_error", tool=tool_name, mcp=mcp_name,
                             error=str(body["error"]), duration_ms=duration_ms)
            return {"error": body["error"], "mcp": mcp_name, "duration_ms": duration_ms}

        result = body.get("result", {})
        gateway_log.emit("tool_call", tool=tool_name, mcp=mcp_name,
                         duration_ms=duration_ms, success=True)
        return {"result": result, "mcp": mcp_name, "duration_ms": duration_ms}

    except requests.RequestException as e:
        duration_ms = int((time.time() - start) * 1000)
        gateway_log.emit("tool_error", tool=tool_name, mcp=mcp_name,
                         error=str(e), duration_ms=duration_ms)
        return {"error": f"MCP {mcp_name} unreachable: {e}", "mcp": mcp_name}
```

- [ ] **Step 2: Commit**

```bash
git add mcp-gateway/router.py
git commit -m "feat(mcp-gateway): add tool router with rate limiting and OpenAI format conversion"
```

---

### Task 7: MCP Gateway — HTTP Server

**Files:**
- Create: `mcp-gateway/server.py`
- Create: `mcp-gateway/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
flask==3.1.*
docker==7.*
requests>=2.31
```

- [ ] **Step 2: Create server.py**

```python
"""MCP Gateway HTTP server.
Exposes registration, tool catalog, tool routing, and status endpoints."""
import logging
import os
import time
import requests as http_requests

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

    # If tools not provided, fetch them from the MCP
    if not tools:
        try:
            resp = http_requests.post(
                address,
                json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
                timeout=10,
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
            "address": mcp["address"],
            "status": mcp["status"],
            "tools": len(mcp.get("tools", [])),
            "last_health_check": mcp.get("last_health_check"),
            "consecutive_failures": mcp.get("consecutive_failures", 0),
        }
    return jsonify(summary)


@app.route("/logs", methods=["GET"])
def logs():
    limit = request.args.get("limit", 50, type=int)
    event_filter = request.args.get("event", None)
    entries = gateway_log.recent(limit=limit, event_filter=event_filter)
    return jsonify({"entries": entries, "count": len(entries)})


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


def main():
    port = int(os.environ.get("GATEWAY_PORT", "8094"))

    # Initialize registry from persisted state
    registry.init()

    # Re-probe all persisted MCPs
    log.info("Re-probing persisted MCPs...")
    for name, mcp in registry.get_all().items():
        try:
            resp = http_requests.get(f"{mcp['address']}/health", timeout=5)
            if resp.status_code == 200:
                registry.record_health_success(name)
                log.info(f"MCP {name}: alive")
                # Refresh tools
                try:
                    tresp = http_requests.post(
                        mcp["address"],
                        json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
                        timeout=10,
                    )
                    tools = tresp.json().get("result", {}).get("tools", [])
                    if tools:
                        registry.update_tools(name, tools)
                except Exception:
                    pass
            else:
                registry.update_status(name, "degraded")
                log.warning(f"MCP {name}: not healthy (status {resp.status_code})")
        except http_requests.RequestException:
            registry.update_status(name, "offline")
            log.warning(f"MCP {name}: unreachable")

    # Start health monitor
    gateway_log.emit("gateway_started", port=port)
    health.start()

    log.info(f"MCP Gateway starting on :{port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add mcp-gateway/server.py mcp-gateway/requirements.txt
git commit -m "feat(mcp-gateway): add Flask HTTP server with all endpoints"
```

---

### Task 8: MCP Gateway — Dockerfile

**Files:**
- Create: `mcp-gateway/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 8094

CMD ["python3", "server.py"]
```

- [ ] **Step 2: Commit**

```bash
git add mcp-gateway/Dockerfile
git commit -m "feat(mcp-gateway): add Dockerfile"
```

---

### Task 9: MCP Base Class

**Files:**
- Create: `mcp-gateway/mcp_base.py`

This is shared by all MCP servers. Provides registration retry, health endpoint, and JSON-RPC dispatch.

- [ ] **Step 1: Create mcp_base.py**

```python
"""Base class for MCP servers.
Handles gateway registration with retry-backoff, health endpoint, and JSON-RPC dispatch."""
import json
import os
import time
import signal
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

log = logging.getLogger("mcp_base")


class MCPServer:
    """Base MCP server with self-registration, health, and JSON-RPC tool dispatch."""

    def __init__(self, name: str, port: int, tools: list[dict]):
        """
        Args:
            name: MCP server name (used for registration)
            port: Port to listen on
            tools: List of MCP tool schemas (name, description, inputSchema)
        """
        self.name = name
        self.port = port
        self.tools = tools
        self._tool_handlers: dict[str, callable] = {}
        self._start_time = time.time()
        self._gateway_url = os.environ.get("GATEWAY_URL", "http://mcp-gateway:8094")
        self._container_name = os.environ.get("HOSTNAME", name)
        self._stop_event = threading.Event()

    def register_handler(self, tool_name: str, handler: callable):
        """Register a handler function for a tool."""
        self._tool_handlers[tool_name] = handler

    def _register_with_gateway(self):
        """Register with gateway, retrying with exponential backoff."""
        delays = [10, 20, 40, 60]  # seconds, capped at 60
        attempt = 0
        while not self._stop_event.is_set():
            try:
                resp = requests.post(
                    f"{self._gateway_url}/register",
                    json={
                        "name": self.name,
                        "address": f"http://{self._container_name}:{self.port}",
                        "container_name": self._container_name,
                        "tools": self.tools,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    log.info(f"Registered with gateway: {resp.json()}")
                    return True
                log.warning(f"Gateway returned {resp.status_code}: {resp.text}")
            except requests.RequestException as e:
                delay = delays[min(attempt, len(delays) - 1)]
                log.warning(f"Gateway unreachable ({e}), retrying in {delay}s...")
                self._stop_event.wait(delay)
                attempt += 1
        return False

    def _deregister(self):
        """Gracefully deregister from gateway."""
        try:
            requests.post(
                f"{self._gateway_url}/deregister",
                json={"name": self.name},
                timeout=5,
            )
            log.info("Deregistered from gateway")
        except requests.RequestException:
            pass

    def _handle_jsonrpc(self, body: dict) -> dict:
        """Handle a JSON-RPC 2.0 request."""
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id", 1)

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": self.name, "version": "1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.tools},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = self._tool_handlers.get(tool_name)
            if not handler:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
            try:
                result = handler(arguments)
                return {"jsonrpc": "2.0", "id": req_id, "result": result}
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(e)},
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def start(self):
        """Start the MCP server: register with gateway, serve JSON-RPC + health."""
        mcp = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    body = json.dumps({
                        "status": "ok",
                        "name": mcp.name,
                        "tools": len(mcp.tools),
                        "uptime": int(time.time() - mcp._start_time),
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                result = mcp._handle_jsonrpc(req)
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # Suppress default HTTP logs

        # Register with gateway in background (retries until successful)
        reg_thread = threading.Thread(target=self._register_with_gateway, daemon=True)
        reg_thread.start()

        # Graceful shutdown
        def shutdown_handler(sig, frame):
            log.info("Shutting down...")
            self._deregister()
            self._stop_event.set()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        # Start HTTP server
        server = HTTPServer(("0.0.0.0", self.port), Handler)
        log.info(f"MCP server '{self.name}' listening on :{self.port}")
        server.serve_forever()
```

- [ ] **Step 2: Commit**

```bash
git add mcp-gateway/mcp_base.py
git commit -m "feat(mcp-gateway): add MCP base class with registration retry and JSON-RPC dispatch"
```

---

### Task 10: Docker MCP — Port Allocator

**Files:**
- Create: `docker-mcp/port_allocator.py`

- [ ] **Step 1: Create port_allocator.py**

```python
"""Auto-detect free host ports for Docker stack deploys."""
import socket
import logging

log = logging.getLogger("port_allocator")

_PORT_RANGE_START = 8000
_PORT_RANGE_END = 9000


def is_port_free(port: int) -> bool:
    """Check if a TCP port is available on the host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("0.0.0.0", port))
            return result != 0
    except OSError:
        return False


def find_free_port(preferred: int = None) -> int:
    """Find a free port. Tries preferred first, then scans the range."""
    if preferred and is_port_free(preferred):
        return preferred

    for port in range(_PORT_RANGE_START, _PORT_RANGE_END):
        if is_port_free(port):
            if preferred and port != preferred:
                log.info(f"Port {preferred} in use, allocated {port} instead")
            return port

    raise RuntimeError(f"No free ports in range {_PORT_RANGE_START}-{_PORT_RANGE_END}")
```

- [ ] **Step 2: Commit**

```bash
git add docker-mcp/port_allocator.py
git commit -m "feat(docker-mcp): add automatic port allocator"
```

---

### Task 11: Docker MCP — Resource Guard

**Files:**
- Create: `docker-mcp/resource_guard.py`

- [ ] **Step 1: Create resource_guard.py**

```python
"""Pre-deploy resource checks: memory and disk."""
import os
import shutil
import logging

log = logging.getLogger("resource_guard")

_MIN_MEMORY_MB = int(os.environ.get("MIN_MEMORY_MB", "400"))
_WARN_MEMORY_MB = int(os.environ.get("WARN_MEMORY_MB", "800"))
_MIN_DISK_GB = float(os.environ.get("MIN_DISK_GB", "2"))
_WARN_DISK_GB = float(os.environ.get("WARN_DISK_GB", "5"))


def get_free_memory_mb() -> int:
    """Get available memory in MB (Linux only)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 9999  # Assume OK if we can't read


def get_free_disk_gb(path: str = "/") -> float:
    """Get free disk space in GB."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except OSError:
        return 999.0


def check_resources() -> dict:
    """Check if resources are sufficient for a deploy.

    Returns:
        {"ok": True/False, "warnings": [...], "errors": [...],
         "memory_mb": N, "disk_gb": N.N}
    """
    mem_mb = get_free_memory_mb()
    disk_gb = get_free_disk_gb()
    warnings = []
    errors = []

    if mem_mb < _MIN_MEMORY_MB:
        errors.append(f"Only {mem_mb}MB memory free (minimum: {_MIN_MEMORY_MB}MB). "
                      "Remove a container or add swap first.")
    elif mem_mb < _WARN_MEMORY_MB:
        warnings.append(f"Low memory: {mem_mb}MB free (recommend: {_WARN_MEMORY_MB}MB+)")

    if disk_gb < _MIN_DISK_GB:
        errors.append(f"Only {disk_gb:.1f}GB disk free (minimum: {_MIN_DISK_GB}GB). "
                      "Clean up or expand storage.")
    elif disk_gb < _WARN_DISK_GB:
        warnings.append(f"Low disk: {disk_gb:.1f}GB free (recommend: {_WARN_DISK_GB}GB+)")

    return {
        "ok": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
        "memory_mb": mem_mb,
        "disk_gb": round(disk_gb, 1),
    }
```

- [ ] **Step 2: Commit**

```bash
git add docker-mcp/resource_guard.py
git commit -m "feat(docker-mcp): add pre-deploy resource guard"
```

---

### Task 12: Docker MCP — Secrets Manager

**Files:**
- Create: `docker-mcp/secrets.py`

- [ ] **Step 1: Create secrets.py**

```python
"""Auto-generate and persist secrets for compose templates."""
import os
import secrets
import string
import logging

log = logging.getLogger("secrets_mgr")

_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/secrets")
_SECRET_KEYWORDS = {"SECRET", "PASSWORD", "KEY", "TOKEN", "PASSPHRASE"}


def _is_secret_var(var_name: str) -> bool:
    """Check if a variable name looks like it holds a secret."""
    upper = var_name.upper()
    return any(kw in upper for kw in _SECRET_KEYWORDS)


def _generate_secret(length: int = 32) -> str:
    """Generate a random alphanumeric secret."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _secrets_file(stack_name: str) -> str:
    return os.path.join(_SECRETS_DIR, f"{stack_name}.env")


def load_secrets(stack_name: str) -> dict:
    """Load persisted secrets for a stack."""
    path = _secrets_file(stack_name)
    result = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip()
    return result


def _save_secrets(stack_name: str, secrets_dict: dict):
    """Persist secrets for a stack."""
    os.makedirs(_SECRETS_DIR, exist_ok=True)
    path = _secrets_file(stack_name)
    with open(path, "w") as f:
        f.write(f"# Auto-generated secrets for {stack_name}\n")
        for key, val in secrets_dict.items():
            f.write(f"{key}={val}\n")
    # Restrict permissions
    os.chmod(path, 0o600)


def resolve_secrets(stack_name: str, template_vars: dict) -> dict:
    """Resolve secrets for a deploy.

    - Reuses persisted secrets from previous deploys
    - Auto-generates missing secrets for variables that look like secrets
    - User-provided values take precedence

    Returns the full vars dict with secrets filled in.
    """
    persisted = load_secrets(stack_name)
    result = dict(template_vars)
    new_secrets = {}

    for var_name, var_value in result.items():
        if var_value and var_value != "changeme":
            continue  # User provided a real value
        if _is_secret_var(var_name):
            if var_name in persisted:
                result[var_name] = persisted[var_name]
                log.debug(f"Reusing persisted secret: {var_name}")
            else:
                generated = _generate_secret()
                result[var_name] = generated
                new_secrets[var_name] = generated
                log.info(f"Generated new secret for: {var_name}")

    # Save any new secrets alongside existing ones
    if new_secrets:
        all_secrets = {**persisted, **new_secrets}
        _save_secrets(stack_name, all_secrets)

    return result
```

- [ ] **Step 2: Commit**

```bash
git add docker-mcp/secrets.py
git commit -m "feat(docker-mcp): add secrets auto-generation and persistence"
```

---

### Task 13: Docker MCP — Template Resolver

**Files:**
- Create: `docker-mcp/templates.py`

- [ ] **Step 1: Create templates.py**

```python
"""Resolve and render compose templates with hot-reload support."""
import os
import re
import string
import logging
import threading

log = logging.getLogger("templates")

_REPO_DIR = os.environ.get("TEMPLATES_REPO_DIR", "/templates/repo")
_LOCAL_DIR = os.environ.get("TEMPLATES_LOCAL_DIR", "/templates/local")

_cache: dict[str, str] = {}
_lock = threading.Lock()


def _scan_templates() -> dict[str, str]:
    """Scan both directories and build name->path mapping.
    Local overrides take precedence over repo templates."""
    templates = {}

    # Repo templates first (lower priority)
    if os.path.isdir(_REPO_DIR):
        for f in os.listdir(_REPO_DIR):
            if f.endswith((".yml", ".yaml")):
                name = f.rsplit(".", 1)[0]
                templates[name] = os.path.join(_REPO_DIR, f)

    # Local overrides (higher priority)
    if os.path.isdir(_LOCAL_DIR):
        for f in os.listdir(_LOCAL_DIR):
            if f.endswith((".yml", ".yaml")):
                name = f.rsplit(".", 1)[0]
                templates[name] = os.path.join(_LOCAL_DIR, f)

    return templates


def refresh():
    """Refresh the template cache."""
    global _cache
    with _lock:
        _cache = _scan_templates()
    log.info(f"Template cache: {len(_cache)} templates ({', '.join(_cache.keys()) or 'none'})")


def list_templates() -> list[str]:
    """List available template names."""
    with _lock:
        if not _cache:
            refresh()
        return list(_cache.keys())


def get_template(name: str) -> str | None:
    """Get the raw YAML content for a template."""
    with _lock:
        if not _cache:
            refresh()
        path = _cache.get(name)
    if path and os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def render_template(name: str, variables: dict) -> str | None:
    """Render a template with variable substitution.
    Supports ${VAR:-default} and ${VAR} syntax."""
    raw = get_template(name)
    if raw is None:
        return None

    def replace_var(match):
        var_name = match.group(1)
        default = match.group(3)  # from ${VAR:-default}
        return str(variables.get(var_name, default or ""))

    # Match ${VAR:-default} and ${VAR}
    rendered = re.sub(r'\$\{(\w+)(:-([^}]*))?\}', replace_var, raw)
    return rendered


def start_watcher():
    """Start a background thread to watch for template changes."""
    try:
        import inotify_simple
        HAS_INOTIFY = True
    except ImportError:
        HAS_INOTIFY = False

    if not HAS_INOTIFY:
        log.info("inotify not available, using polling for template changes")
        # Fallback: poll every 30s
        def poll_loop():
            while True:
                import time
                time.sleep(30)
                refresh()
        t = threading.Thread(target=poll_loop, daemon=True)
        t.start()
        return

    def watch_loop():
        import inotify_simple
        inotify = inotify_simple.INotify()
        flags = inotify_simple.flags.CREATE | inotify_simple.flags.MODIFY | inotify_simple.flags.DELETE

        for d in [_REPO_DIR, _LOCAL_DIR]:
            if os.path.isdir(d):
                inotify.add_watch(d, flags)

        while True:
            events = inotify.read(timeout=60000)
            if events:
                refresh()
                log.info("Templates reloaded after file change")

    t = threading.Thread(target=watch_loop, daemon=True, name="template-watcher")
    t.start()


# Initial load
refresh()
```

- [ ] **Step 2: Commit**

```bash
git add docker-mcp/templates.py
git commit -m "feat(docker-mcp): add template resolver with hot-reload"
```

---

### Task 14: Docker MCP — Tool Implementations

**Files:**
- Create: `docker-mcp/tools.py`

- [ ] **Step 1: Create tools.py**

```python
"""Docker tool implementations for the Docker MCP server."""
import json
import time
import logging
import subprocess

import docker

import templates
import port_allocator
import resource_guard
import secrets as secrets_mgr

log = logging.getLogger("docker_tools")

_client = None


def _docker():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


# ── Tool: list_containers ──────────────────────────────────────────────────

def list_containers(args: dict) -> dict:
    """List all containers, optionally filtered by name pattern."""
    name_filter = args.get("filter", "")
    containers = _docker().containers.list(all=True)
    result = []
    for c in containers:
        if name_filter and name_filter.lower() not in c.name.lower():
            continue
        result.append({
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            "ports": {str(k): str(v) for k, v in (c.ports or {}).items() if v},
            "created": c.attrs.get("Created", ""),
        })
    return {"containers": result, "count": len(result)}


# ── Tool: container_status ─────────────────────────────────────────────────

def container_status(args: dict) -> dict:
    """Get detailed status of a container."""
    name = args.get("name")
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}

    state = c.attrs.get("State", {})
    net = c.attrs.get("NetworkSettings", {})
    mounts = c.attrs.get("Mounts", [])
    return {
        "name": c.name,
        "status": c.status,
        "health": state.get("Health", {}).get("Status", "none"),
        "started_at": state.get("StartedAt", ""),
        "restart_count": c.attrs.get("RestartCount", 0),
        "image": c.image.tags[0] if c.image.tags else c.image.short_id,
        "ports": {str(k): str(v) for k, v in (c.ports or {}).items() if v},
        "mounts": [{"source": m.get("Source", ""), "destination": m.get("Destination", "")}
                   for m in mounts],
        "networks": list(net.get("Networks", {}).keys()),
    }


# ── Tool: container_logs ───────────────────────────────────────────────────

def container_logs(args: dict) -> dict:
    """Get recent logs from a container."""
    name = args.get("name")
    tail = args.get("tail", 50)
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}

    logs = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
    return {"name": name, "logs": logs, "lines": len(logs.strip().split("\n"))}


# ── Tool: restart_container ────────────────────────────────────────────────

def restart_container(args: dict) -> dict:
    """Restart a container."""
    name = args.get("name")
    if not name:
        raise ValueError("container name required")
    try:
        c = _docker().containers.get(name)
        c.restart(timeout=30)
        return {"name": name, "status": "restarted"}
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}


# ── Tool: remove_container ─────────────────────────────────────────────────

def remove_container(args: dict) -> dict:
    """Stop and remove a container."""
    name = args.get("name")
    remove_volumes = args.get("remove_volumes", False)
    if not name:
        raise ValueError("container name required")

    # Safety: never remove critical containers
    protected = {"chaguli", "mcp-gateway", "docker-mcp", "llama-server"}
    if name in protected:
        return {"error": f"Cannot remove protected container '{name}'"}

    try:
        c = _docker().containers.get(name)
        c.stop(timeout=30)
        c.remove(v=remove_volumes)
        return {"name": name, "status": "removed", "volumes_removed": remove_volumes}
    except docker.errors.NotFound:
        return {"error": f"Container '{name}' not found"}


# ── Tool: deploy_stack ─────────────────────────────────────────────────────

def deploy_stack(args: dict) -> dict:
    """Deploy a compose stack from template or raw YAML.

    If template exists: deploy directly.
    If compose_yaml provided and no template: return with require_approval=True.
    """
    stack_name = args.get("name")
    template_name = args.get("template")
    compose_yaml = args.get("compose_yaml")
    variables = args.get("vars", {})
    approved = args.get("approved", False)

    if not stack_name:
        raise ValueError("stack name required")

    # Resource check
    resources = resource_guard.check_resources()
    if not resources["ok"]:
        return {
            "status": "refused",
            "reason": "; ".join(resources["errors"]),
            "memory_mb": resources["memory_mb"],
            "disk_gb": resources["disk_gb"],
        }

    # Resolve template
    rendered_yaml = None
    from_template = False

    if template_name:
        # Resolve secrets in variables
        variables = secrets_mgr.resolve_secrets(stack_name, variables)
        rendered_yaml = templates.render_template(template_name, variables)
        if rendered_yaml:
            from_template = True
        else:
            return {
                "status": "no_template",
                "message": f"No template found for '{template_name}'. Available: {', '.join(templates.list_templates())}",
                "available_templates": templates.list_templates(),
            }

    if compose_yaml and not from_template:
        if not approved:
            return {
                "status": "approval_required",
                "compose_yaml": compose_yaml,
                "message": f"No template for '{stack_name}'. Review this compose YAML and approve.",
                "require_approval": True,
            }
        rendered_yaml = compose_yaml

    if not rendered_yaml:
        return {
            "status": "error",
            "message": "Provide either a template name or compose_yaml",
            "available_templates": templates.list_templates(),
        }

    # Find free port if template uses PORT variable
    if "${PORT" in rendered_yaml or "PORT" in variables:
        preferred = int(variables.get("PORT", 8010))
        actual_port = port_allocator.find_free_port(preferred)
        rendered_yaml = rendered_yaml.replace(f"${{{preferred}}}", str(actual_port))
        variables["PORT"] = str(actual_port)
        # Re-render with actual port
        if from_template and template_name:
            rendered_yaml = templates.render_template(template_name, variables)

    # Write compose file and deploy
    import tempfile
    import os

    deploy_dir = f"/data/stacks/{stack_name}"
    os.makedirs(deploy_dir, exist_ok=True)
    compose_path = os.path.join(deploy_dir, "docker-compose.yml")

    with open(compose_path, "w") as f:
        f.write(rendered_yaml)

    # Run docker compose up
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_path, "-p", stack_name, "up", "-d"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {
                "status": "deploy_failed",
                "stderr": result.stderr[-500:],
                "stdout": result.stdout[-500:],
            }
    except subprocess.TimeoutExpired:
        return {"status": "deploy_timeout", "message": "Deploy timed out after 120s"}

    # Post-deploy health verification
    time.sleep(10)
    try:
        containers = _docker().containers.list(
            filters={"label": f"com.docker.compose.project={stack_name}"}
        )
        if not containers:
            # Try by name pattern
            containers = [c for c in _docker().containers.list(all=True)
                         if stack_name.replace("-", "") in c.name.replace("-", "")]

        crash_looping = []
        healthy = []
        for c in containers:
            c.reload()
            if c.attrs.get("RestartCount", 0) > 2:
                logs = c.logs(tail=20).decode("utf-8", errors="replace")
                crash_looping.append({"name": c.name, "logs": logs})
            elif c.status == "running":
                healthy.append(c.name)

        if crash_looping:
            return {
                "status": "deployed_but_failing",
                "crash_looping": crash_looping,
                "offer_rollback": True,
                "message": "Containers deployed but crash-looping. Want me to roll back?",
            }

        # Determine the URL
        port = variables.get("PORT", "")
        host_ip = "192.168.29.10"
        url = f"http://{host_ip}:{port}" if port else None

        warnings = resources.get("warnings", [])

        return {
            "status": "healthy",
            "containers": healthy,
            "url": url,
            "warnings": warnings,
            "from_template": from_template,
        }

    except Exception as e:
        return {
            "status": "deployed_unknown",
            "message": f"Deploy succeeded but health check failed: {e}",
        }
```

- [ ] **Step 2: Commit**

```bash
git add docker-mcp/tools.py
git commit -m "feat(docker-mcp): add 6 Docker tool implementations with resource guard and approval flow"
```

---

### Task 15: Docker MCP — Server + Dockerfile

**Files:**
- Create: `docker-mcp/server.py`
- Create: `docker-mcp/requirements.txt`
- Create: `docker-mcp/Dockerfile`

- [ ] **Step 1: Create requirements.txt**

```
docker==7.*
pyyaml>=6.0
requests>=2.31
inotify-simple>=1.3; sys_platform == 'linux'
```

- [ ] **Step 2: Create server.py**

```python
"""Docker MCP server. Provides container management tools via JSON-RPC."""
import os
import sys
import logging

# Add mcp-gateway to path for mcp_base
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

from mcp_base import MCPServer
import tools
import templates

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("docker-mcp")

TOOL_SCHEMAS = [
    {
        "name": "list_containers",
        "description": "List all Docker containers (running and stopped). Use to check what's deployed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional name pattern to filter by"},
            },
        },
    },
    {
        "name": "deploy_stack",
        "description": "Deploy a Docker Compose stack. Uses vetted templates when available, requires approval for custom YAML.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Stack name (e.g., 'paperless-ngx')"},
                "template": {"type": "string", "description": "Template name (e.g., 'paperless-ngx'). List available with list_containers."},
                "compose_yaml": {"type": "string", "description": "Raw docker-compose YAML (used when no template exists)"},
                "vars": {"type": "object", "description": "Template variables (e.g., {PORT: '8010', DATA_DIR: '/opt/paperless'})"},
                "approved": {"type": "boolean", "description": "Set to true when user has approved an LLM-generated compose YAML"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_container",
        "description": "Stop and remove a Docker container. Cannot remove protected containers (chaguli, mcp-gateway, llama-server).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name to remove"},
                "remove_volumes": {"type": "boolean", "description": "Also remove associated volumes (default: false)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "container_logs",
        "description": "Get recent logs from a Docker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
                "tail": {"type": "integer", "description": "Number of log lines (default: 50)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "container_status",
        "description": "Get detailed status of a Docker container including health, ports, mounts, and uptime.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "restart_container",
        "description": "Restart a Docker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name to restart"},
            },
            "required": ["name"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8095"))

    # Start template watcher
    templates.start_watcher()
    log.info(f"Templates: {', '.join(templates.list_templates()) or 'none'}")

    # Create MCP server
    server = MCPServer(name="docker", port=port, tools=TOOL_SCHEMAS)

    # Register tool handlers
    server.register_handler("list_containers", tools.list_containers)
    server.register_handler("deploy_stack", tools.deploy_stack)
    server.register_handler("remove_container", tools.remove_container)
    server.register_handler("container_logs", tools.container_logs)
    server.register_handler("container_status", tools.container_status)
    server.register_handler("restart_container", tools.restart_container)

    log.info(f"Docker MCP starting on :{port} with {len(TOOL_SCHEMAS)} tools")
    server.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

# Install docker CLI for compose commands
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io docker-compose-v2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/stacks /secrets /templates/repo /templates/local

EXPOSE 8095

CMD ["python3", "server.py"]
```

- [ ] **Step 4: Commit**

```bash
git add docker-mcp/server.py docker-mcp/requirements.txt docker-mcp/Dockerfile
git commit -m "feat(docker-mcp): add MCP server with tool schemas and Dockerfile"
```

---

### Task 16: Compose Templates

**Files:**
- Create: `templates/docker/paperless-ngx.yml`
- Create: `templates/docker/uptime-kuma.yml`

- [ ] **Step 1: Create paperless-ngx.yml**

```yaml
version: "3.8"
services:
  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    container_name: paperless-ngx
    ports:
      - "${PORT:-8010}:8000"
    volumes:
      - ${DATA_DIR:-/opt/paperless}/data:/usr/src/paperless/data
      - ${DATA_DIR:-/opt/paperless}/media:/usr/src/paperless/media
      - ${DATA_DIR:-/opt/paperless}/consume:/usr/src/paperless/consume
    environment:
      PAPERLESS_SECRET_KEY: ${SECRET_KEY:-changeme}
      PAPERLESS_URL: http://${HOST_IP:-192.168.29.10}:${PORT:-8010}
      PAPERLESS_ADMIN_USER: ${ADMIN_USER:-admin}
      PAPERLESS_ADMIN_PASSWORD: ${ADMIN_PASSWORD:-changeme}
    restart: unless-stopped
```

- [ ] **Step 2: Create uptime-kuma.yml**

```yaml
version: "3.8"
services:
  uptime-kuma:
    image: louislam/uptime-kuma:latest
    container_name: uptime-kuma
    ports:
      - "${PORT:-3001}:3001"
    volumes:
      - ${DATA_DIR:-/opt/uptime-kuma}/data:/app/data
    restart: unless-stopped
```

- [ ] **Step 3: Commit**

```bash
git add templates/docker/paperless-ngx.yml templates/docker/uptime-kuma.yml
git commit -m "feat: add compose templates for Paperless-ngx and Uptime Kuma"
```

---

### Task 17: Docker Compose for Gateway + Docker MCP

**Files:**
- Create: `docker-compose.mcp.yml`

- [ ] **Step 1: Create docker-compose.mcp.yml**

```yaml
version: "3.8"

networks:
  chaguli-net:
    external: true

services:
  mcp-gateway:
    build: ./mcp-gateway
    container_name: mcp-gateway
    ports:
      - "8094:8094"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./mcp-gateway/data:/data
      - ./templates/docker:/templates/repo:ro
      - ~/mcp-gateway/templates:/templates/local
      - ./mcp-gateway/mcp_base.py:/mcp-base/mcp_base.py:ro
    environment:
      - GATEWAY_PORT=8094
      - HEALTH_CHECK_INTERVAL=60
      - CHAGULI_ALERTS_DIR=/data/alerts
      - GATEWAY_STATE_FILE=/data/gateway_state.json
      - GATEWAY_LOG_FILE=/data/gateway.log
    networks:
      - chaguli-net
    restart: unless-stopped

  docker-mcp:
    build: ./docker-mcp
    container_name: docker-mcp
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./templates/docker:/templates/repo:ro
      - ~/mcp-gateway/templates:/templates/local
      - ~/mcp-gateway/secrets:/secrets
      - ./mcp-gateway/mcp_base.py:/mcp-base/mcp_base.py:ro
      - mcp-stacks:/data/stacks
    environment:
      - MCP_PORT=8095
      - GATEWAY_URL=http://mcp-gateway:8094
      - MCP_BASE_DIR=/mcp-base
      - TEMPLATES_REPO_DIR=/templates/repo
      - TEMPLATES_LOCAL_DIR=/templates/local
      - SECRETS_DIR=/secrets
    networks:
      - chaguli-net
    restart: unless-stopped
    depends_on:
      - mcp-gateway

volumes:
  mcp-stacks:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.mcp.yml
git commit -m "feat: add docker-compose for MCP gateway + Docker MCP"
```

---

### Task 18: Bootstrap Script

**Files:**
- Create: `scripts/setup_mcp_gateway.sh`

- [ ] **Step 1: Create setup_mcp_gateway.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# setup_mcp_gateway.sh — One-command bootstrap for MCP Gateway + Docker MCP
#
# Usage: bash setup_mcp_gateway.sh
# Idempotent — safe to re-run.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== MCP Gateway Bootstrap ==="
echo "Project: $PROJECT_DIR"

# ── Step 1: Create Docker network ──────────────────────────────────────────
echo ""
echo "[1/8] Creating Docker network 'chaguli-net'..."
if docker network inspect chaguli-net >/dev/null 2>&1; then
    echo "  Already exists"
else
    docker network create chaguli-net
    echo "  Created"
fi

# ── Step 2: Connect Chaguli to the network ─────────────────────────────────
echo ""
echo "[2/8] Connecting Chaguli to chaguli-net..."
if docker inspect chaguli --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | grep -q chaguli-net; then
    echo "  Already connected"
else
    if docker ps --format '{{.Names}}' | grep -q '^chaguli$'; then
        docker network connect chaguli-net chaguli
        echo "  Connected"
    else
        echo "  WARNING: Chaguli container not found. Connect manually after starting it:"
        echo "  docker network connect chaguli-net chaguli"
    fi
fi

# ── Step 3: Create local directories ──────────────────────────────────────
echo ""
echo "[3/8] Creating local directories..."
mkdir -p ~/mcp-gateway/templates
mkdir -p ~/mcp-gateway/secrets
chmod 700 ~/mcp-gateway/secrets
echo "  ~/mcp-gateway/templates/ (local template overrides)"
echo "  ~/mcp-gateway/secrets/   (auto-generated secrets)"

# ── Step 4: Create data directory ──────────────────────────────────────────
echo ""
echo "[4/8] Creating data directory..."
mkdir -p "$PROJECT_DIR/mcp-gateway/data"
echo "  $PROJECT_DIR/mcp-gateway/data/"

# ── Step 5: Build images ──────────────────────────────────────────────────
echo ""
echo "[5/8] Building Docker images..."
cd "$PROJECT_DIR"
docker compose -f docker-compose.mcp.yml build

# ── Step 6: Deploy ─────────────────────────────────────────────────────────
echo ""
echo "[6/8] Deploying gateway + Docker MCP..."
docker compose -f docker-compose.mcp.yml up -d

# ── Step 7: Wait for readiness ─────────────────────────────────────────────
echo ""
echo "[7/8] Waiting for gateway readiness..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8094/status >/dev/null 2>&1; then
        echo "  Gateway is ready!"
        break
    fi
    if [ "$i" = "30" ]; then
        echo "  ERROR: Gateway did not become ready in 60s"
        echo "  Check logs: docker logs mcp-gateway"
        exit 1
    fi
    sleep 2
done

# Wait a bit more for Docker MCP to register
sleep 5

# ── Step 8: Verify ─────────────────────────────────────────────────────────
echo ""
echo "[8/8] Verifying..."

# Check gateway status
echo ""
echo "Gateway status:"
curl -sf http://localhost:8094/status | python3 -m json.tool 2>/dev/null || echo "  Could not reach gateway"

# Check registered MCPs
echo ""
echo "Registered MCPs:"
curl -sf http://localhost:8094/mcps | python3 -m json.tool 2>/dev/null || echo "  Could not reach gateway"

# Smoke test: list containers
echo ""
echo "Smoke test — list_containers:"
RESULT=$(curl -sf -X POST http://localhost:8094/tools/call \
    -H "Content-Type: application/json" \
    -d '{"name": "list_containers", "arguments": {}}' 2>/dev/null || echo '{"error": "failed"}')
echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"

echo ""
echo "=== MCP Gateway Bootstrap Complete ==="
echo ""
echo "Gateway:    http://localhost:8094"
echo "Docker MCP: http://localhost:8095 (internal)"
echo ""
echo "Templates:  $PROJECT_DIR/templates/docker/ (repo)"
echo "            ~/mcp-gateway/templates/        (local overrides)"
echo "Secrets:    ~/mcp-gateway/secrets/"
echo ""
echo "Next steps:"
echo "  1. Patch Chaguli to use the gateway (run integrate_mcp_bridge.py)"
echo "  2. Test via Telegram: 'list my containers'"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/setup_mcp_gateway.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/setup_mcp_gateway.sh
git commit -m "feat: add one-command MCP gateway bootstrap script"
```

---

### Task 19: Chaguli Integration Bridge

**Files:**
- Create: `scripts/integrate_mcp_bridge.py`

This patches Chaguli's `agentharness_tools.py` to route MCP tool calls through the gateway.

- [ ] **Step 1: Create integrate_mcp_bridge.py**

```python
#!/usr/bin/env python3
"""Patch Chaguli to route tool calls through the MCP gateway.
Adds a thin bridge: fetches tool catalog from gateway, merges into TOOL_SCHEMAS,
routes dispatched calls through the gateway.

Run on homelab: python3 integrate_mcp_bridge.py
"""
import os

# Target: the agentharness_tools.py that's already patched into Chaguli
AH_TOOLS = os.path.expanduser("~/openclaw/chaguli/agentharness_tools.py")
if not os.path.exists(AH_TOOLS):
    print(f"agentharness_tools.py not found at {AH_TOOLS}")
    print("Run integrate_chaguli.sh first, then run this script.")
    exit(1)

with open(AH_TOOLS) as f:
    code = f.read()

changes = 0

# ── Step 1: Add MCP gateway bridge ────────────────────────────────────────

mcp_bridge = '''
# ── MCP Gateway Bridge ────────────────────────────────────────────────────
import requests as _mcp_requests
import threading as _mcp_threading
import time as _mcp_time
import logging as _mcp_logging

_mcp_log = _mcp_logging.getLogger("mcp_bridge")
_MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://mcp-gateway:8094")
_mcp_tool_catalog = []
_mcp_tool_names = set()
_mcp_catalog_cache = {"tools": [], "addresses": {}}  # Fallback cache


def _mcp_fetch_catalog():
    """Fetch tool catalog from MCP gateway."""
    global _mcp_tool_catalog, _mcp_tool_names
    try:
        resp = _mcp_requests.get(f"{_MCP_GATEWAY_URL}/tools/catalog", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _mcp_tool_catalog = data.get("tools", [])
            _mcp_tool_names = {t["function"]["name"] for t in _mcp_tool_catalog if "function" in t}
            # Update fallback cache
            _mcp_catalog_cache["tools"] = list(_mcp_tool_catalog)
            _mcp_log.debug(f"MCP catalog: {len(_mcp_tool_catalog)} tools")
    except _mcp_requests.RequestException as e:
        _mcp_log.warning(f"MCP gateway unreachable: {e}")
        # Use cached catalog as fallback
        if _mcp_catalog_cache["tools"]:
            _mcp_tool_catalog = _mcp_catalog_cache["tools"]
            _mcp_tool_names = {t["function"]["name"] for t in _mcp_tool_catalog if "function" in t}
            _mcp_log.info(f"Using cached MCP catalog: {len(_mcp_tool_catalog)} tools")


def _mcp_catalog_refresh_loop():
    """Background thread to refresh MCP tool catalog every 60s."""
    while True:
        _mcp_time.sleep(60)
        _mcp_fetch_catalog()


# Initial fetch
_mcp_fetch_catalog()

# Start background refresh
_mcp_refresh_thread = _mcp_threading.Thread(target=_mcp_catalog_refresh_loop, daemon=True)
_mcp_refresh_thread.start()


def _mcp_dispatch(tool_name: str, tool_args: dict) -> str:
    """Route a tool call through the MCP gateway."""
    try:
        resp = _mcp_requests.post(
            f"{_MCP_GATEWAY_URL}/tools/call",
            json={"name": tool_name, "arguments": tool_args},
            timeout=120,
        )
        data = resp.json()
        if "error" in data:
            if data["error"] == "rate_limited":
                return f"Too many requests. Try again in {data.get('retry_after_seconds', 60)}s."
            return f"MCP error: {data['error']}"
        result = data.get("result", {})
        # Format result for Telegram display
        if isinstance(result, dict):
            import json
            return json.dumps(result, indent=2)
        return str(result)
    except _mcp_requests.RequestException as e:
        return f"MCP gateway unreachable: {e}"


def get_mcp_tools() -> list:
    """Get current MCP tool schemas for merging into TOOL_SCHEMAS."""
    return list(_mcp_tool_catalog)


def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to an MCP server."""
    return tool_name in _mcp_tool_names

'''

if "_MCP_GATEWAY_URL" not in code:
    # Add at the end of the file, before the last newline
    code = code.rstrip() + "\n" + mcp_bridge
    changes += 1
    print("[1] Added MCP gateway bridge")
else:
    print("[1] SKIP — MCP bridge already present")

# ── Step 2: Patch TOOL_SCHEMAS to include MCP tools ───────────────────────

# Find where TOOL_SCHEMAS is defined and add MCP tools
if "get_mcp_tools()" not in code and "TOOL_SCHEMAS" in code:
    # Add MCP tools after TOOL_SCHEMAS definition
    code += """
# Merge MCP tools into TOOL_SCHEMAS
TOOL_SCHEMAS.extend(get_mcp_tools())
"""
    changes += 1
    print("[2] Added MCP tools merge into TOOL_SCHEMAS")
else:
    print("[2] SKIP — already merged or TOOL_SCHEMAS not found")

# ── Step 3: Patch dispatch to route MCP tools ─────────────────────────────

if "is_mcp_tool" not in code and "def dispatch" in code:
    # Find the dispatch function and add MCP routing at the top
    old_dispatch = "def dispatch(tool_name: str, tool_args: dict) -> str:"
    if old_dispatch in code:
        # Find the first line of the function body
        idx = code.index(old_dispatch)
        body_start = code.index("\n", idx) + 1
        # Get the indent
        next_line = code[body_start:code.index("\n", body_start)]
        indent = ""
        for ch in next_line:
            if ch in " \t":
                indent += ch
            else:
                break

        mcp_route = f'''{indent}# Route MCP tools through gateway
{indent}if is_mcp_tool(tool_name):
{indent}    return _mcp_dispatch(tool_name, tool_args)
'''
        code = code[:body_start] + mcp_route + code[body_start:]
        changes += 1
        print("[3] Patched dispatch() to route MCP tools through gateway")
    else:
        print("[3] SKIP — dispatch function signature not found")
else:
    print("[3] SKIP — already patched or dispatch not found")

# ── Write ──────────────────────────────────────────────────────────────────

if changes > 0:
    with open(AH_TOOLS) as f:
        pass  # Already read
    with open(AH_TOOLS, "w") as f:
        f.write(code)
    print(f"\nApplied {changes} changes to agentharness_tools.py")
    print("Restart chaguli to activate: docker restart chaguli")
else:
    print("\nNo changes applied")
```

- [ ] **Step 2: Commit**

```bash
git add scripts/integrate_mcp_bridge.py
git commit -m "feat: add Chaguli MCP bridge integration script"
```

---

### Task 20: End-to-End Verification

- [ ] **Step 1: Build and deploy locally**

```bash
cd /path/to/AgentHarness
# Build
docker compose -f docker-compose.mcp.yml build 2>&1

# Verify images built
docker images | grep -E "mcp-gateway|docker-mcp"
```

Expected: Two images listed.

- [ ] **Step 2: Verify file structure**

```bash
ls -la mcp-gateway/*.py docker-mcp/*.py templates/docker/*.yml docker-compose.mcp.yml scripts/setup_mcp_gateway.sh scripts/integrate_mcp_bridge.py
```

Expected: All files present.

- [ ] **Step 3: Verify no import errors**

```bash
cd mcp-gateway && python3 -c "import gateway_log; import rate_limiter; import registry; print('gateway modules OK')"
cd ../docker-mcp && python3 -c "import port_allocator; import resource_guard; import secrets; import templates; print('docker-mcp modules OK')"
```

Expected: Both print OK.

- [ ] **Step 4: Final commit — all files**

```bash
git add -A
git status
git commit -m "feat: MCP Gateway + Docker MCP — complete implementation

- MCP Gateway: registry, health monitor, tool router, rate limiter, structured logging
- Docker MCP: 6 tools (list, deploy, remove, logs, status, restart)
- Compose templates: Paperless-ngx, Uptime Kuma
- Self-registration with retry-backoff
- Auto-recovery: restart failed MCPs via Docker socket
- Resource guard: memory/disk checks before deploys
- Secrets auto-generation and persistence
- Template hot-reload via inotify
- One-command bootstrap script
- Chaguli integration bridge"
```
