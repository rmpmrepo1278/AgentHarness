"""Health monitor for registered MCP servers.
Adaptive frequency, auto-restart via Docker, escalation to user."""
import threading
import logging
import requests

import docker

import registry
import gateway_log
import notify

log = logging.getLogger("health")

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
    try:
        resp = requests.get(f"{mcp['address']}/health", timeout=5)
        if resp.status_code == 200:
            return True
        # For mcp servers that use OAuth, 401/404 means the server is running but auth/need config
        if resp.status_code in (401, 404):
            return True
        return False
    except requests.RequestException:
        return False


def _restart_container(container_name: str) -> bool:
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
    return {
        "healthy": _INTERVAL_HEALTHY,
        "degraded": _INTERVAL_DEGRADED,
        "offline": _INTERVAL_POST_RESTART,
        "failed": _INTERVAL_FAILED,
        "unknown": 5,
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
                try:
                    resp = requests.post(
                        mcp["address"],
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
                if mcp.get("status") != "failed":
                    registry.update_status(name, "failed")
                    notify.send_alert(
                        f"MCP {name} failed to recover",
                        f"MCP server '{name}' did not recover after automatic restart. Tools are disabled.",
                        severity="error",
                    )


def run(stop_event: threading.Event = None):
    stop = stop_event or _stop_event
    gateway_log.emit("health_monitor_started")
    log.info("Health monitor started")

    while not stop.is_set():
        check_all()
        mcps = registry.get_all()
        if mcps:
            interval = min(_get_interval(m["status"]) for m in mcps.values())
        else:
            interval = _INTERVAL_HEALTHY
        stop.wait(interval)


def start() -> threading.Thread:
    t = threading.Thread(target=run, daemon=True, name="health-monitor")
    t.start()
    return t


def stop():
    _stop_event.set()
