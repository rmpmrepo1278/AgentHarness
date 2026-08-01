"""Homelab Ops MCP server — Docker, systemd, disk, health checks for the homelab.

Exposes common homelab operations as MCP tools so AI agents (Hermes, Claude Code)
can inspect and manage the infrastructure via tool-calling.
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
import logging
from pathlib import Path

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homelab-ops-mcp")

DATA_DIR = Path(os.environ.get("HEALTH_DATA_DIR", "/home/rohit/agentharness/data"))
DASHBOARD_FILE = DATA_DIR / "health_dashboard.json"
CONTAINERS_FILE = DATA_DIR / "containers.json"

# ── Docker helpers ──────────────────────────────────────────────────────────────

def _docker(args: list[str], timeout: int = 15) -> dict:
    """Run a docker command, return {ok, stdout, stderr}."""
    try:
        r = subprocess.run(
            ["docker"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def docker_status(args) -> dict:
    """Get overall Docker status — healthy/unhealthy counts, disk usage."""
    result = _docker(["ps", "--format", "{{.Names}}\t{{.Status}}"])
    if not result["ok"]:
        return {"error": result["stderr"]}

    running = unhealthy = restarting = 0
    containers = []
    for line in result["stdout"].split("\n"):
        if "\t" not in line:
            continue
        name, status = line.split("\t", 1)
        containers.append({"name": name, "status": status})
        if "unhealthy" in status:
            unhealthy += 1
        elif "restarting" in status.lower():
            restarting += 1
        elif "Up" in status:
            running += 1

    disk = _docker(["system", "df", "--format", "{{.Reclaimable}}"])
    return {
        "running": running,
        "unhealthy": unhealthy,
        "restarting": restarting,
        "total": len(containers),
        "disk_reclaimable": disk["stdout"] if disk["ok"] else "unknown",
        "containers": containers[:30],  # cap for tool output size
    }


def docker_container_logs(args) -> dict:
    """Get recent logs for a container."""
    container = args.get("container", "")
    tail = args.get("tail", "50")
    if not container:
        return {"error": "container name required"}
    result = _docker(["logs", f"--tail={tail}", container], timeout=10)
    return {"container": container, "logs": result["stdout"][-4000:] if result["ok"] else result["stderr"]}


def docker_container_restart(args) -> dict:
    """Restart a container."""
    container = args.get("container", "")
    timeout = args.get("timeout", "30")
    if not container:
        return {"error": "container name required"}
    result = _docker(["restart", f"--time={timeout}", container], timeout=60)
    return {"container": container, "restarted": result["ok"], "output": result["stdout"] if result["ok"] else result["stderr"]}


def docker_container_stop(args) -> dict:
    """Stop a container."""
    container = args.get("container", "")
    timeout = args.get("timeout", "30")
    if not container:
        return {"error": "container name required"}
    result = _docker(["stop", f"--time={timeout}", container], timeout=60)
    return {"container": container, "stopped": result["ok"]}


# ── System / disk helpers ───────────────────────────────────────────────────────

def disk_usage(args) -> dict:
    """Get disk usage for key mount points."""
    mounts = args.get("mounts", "/,/home,/mnt/usb,/var/lib/docker")
    results = []
    for mount in mounts.split(","):
        mount = mount.strip()
        if not mount:
            continue
        try:
            r = subprocess.run(
                ["df", "-h", mount], capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                lines = r.stdout.strip().split("\n")
                if len(lines) >= 2:
                    parts = lines[1].split()
                    results.append({
                        "mount": mount,
                        "size": parts[1] if len(parts) > 1 else "?",
                        "used": parts[2] if len(parts) > 2 else "?",
                        "available": parts[3] if len(parts) > 3 else "?",
                        "pct": parts[4] if len(parts) > 4 else "?",
                    })
        except Exception as e:
            results.append({"mount": mount, "error": str(e)})
    return {"disks": results}


def system_load(args) -> dict:
    """Get system load + memory by reading /proc (no uptime/free binary needed in slim)."""
    try:
        load_parts = Path("/proc/loadavg").read_text().split()
        loads = load_parts[:3]

        mem_kb: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                mem_kb[k.strip()] = int(v.split()[0])
        total = mem_kb.get("MemTotal", 0)
        available = mem_kb.get("MemAvailable", 0)

        uptime_s = float(Path("/proc/uptime").read_text().split()[0])
        days = int(uptime_s // 86400)
        hours = int((uptime_s % 86400) // 3600)

        return {
            "uptime": f"up {days}d {hours}h",
            "load_averages": loads,
            "memory": {
                "total_mb": total // 1024,
                "available_mb": available // 1024,
                "used_mb": (total - available) // 1024,
            },
        }
    except Exception as e:
        return {"error": str(e)}


# ── Health check helpers ────────────────────────────────────────────────────────

def homelab_health(args) -> dict:
    """Get homelab health score and key metrics."""
    if DASHBOARD_FILE.exists():
        try:
            data = json.loads(DASHBOARD_FILE.read_text())
            return data
        except Exception:
            pass
    # Fallback: compute on the fly
    return {"score": -1, "checks": {"error": "health_dashboard.json not found"}}


def container_health(args) -> dict:
    """Check docker container health — restarting / unhealthy / missing.

    Replaces a systemctl-based check: systemctl cannot run inside a container
    (requires host PID1 + system bus). Docker health is the signal that actually
    matters for the homelab; systemd-native services are covered by autonomous_fixer
    and healthchecks. Returns per-state container lists + a quick summary."""
    result = _docker(["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.State}}"])
    if not result["ok"]:
        return {"error": result["stderr"]}
    by_state: dict[str, list[str]] = {"healthy": [], "unhealthy": [], "restarting": [], "exited": [], "other": []}
    for line in result["stdout"].split("\n"):
        if "\t" not in line:
            continue
        parts = line.split("\t")
        name, status, state = parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""
        if "unhealthy" in status.lower():
            by_state["unhealthy"].append(name)
        elif "restarting" in status.lower():
            by_state["restarting"].append(name)
        elif state == "running" and "health" in status.lower():
            by_state["healthy"].append(name)
        elif state == "running":
            by_state["other"].append(name)
        elif state == "exited":
            by_state["exited"].append(name)
        else:
            by_state["other"].append(name)
    problems = len(by_state["unhealthy"]) + len(by_state["restarting"]) + len(by_state["exited"])
    return {"summary": {"problems": problems, "total": sum(len(v) for v in by_state.values())}, "containers": by_state}


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("MCP_PORT", "8120"))

    tools = [
        {
            "name": "docker_status",
            "description": "Get Docker container status counts, disk usage, and per-container state",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "docker_container_logs",
            "description": "Get recent logs from a container",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name"},
                    "tail": {"type": "string", "description": "Number of lines (default 50)"},
                },
                "required": ["container"],
            },
        },
        {
            "name": "docker_container_restart",
            "description": "Restart a container gracefully",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name"},
                    "timeout": {"type": "string", "description": "Seconds to wait before SIGKILL (default 30)"},
                },
                "required": ["container"],
            },
        },
        {
            "name": "docker_container_stop",
            "description": "Stop a container",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name"},
                    "timeout": {"type": "string", "description": "Seconds to wait (default 30)"},
                },
                "required": ["container"],
            },
        },
        {
            "name": "disk_usage",
            "description": "Get disk usage for mount points",
            "parameters": {
                "type": "object",
                "properties": {
                    "mounts": {"type": "string", "description": "Comma-separated mount points (default: /,/home,/mnt/usb)"},
                },
            },
        },
        {
            "name": "system_load",
            "description": "Get system load averages and memory usage",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "homelab_health",
            "description": "Get homelab health score and key metrics from health_dashboard.json",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "container_health",
            "description": "Categorize containers by state: healthy, unhealthy, restarting, exited (replaces systemctl checks)",
            "parameters": {"type": "object", "properties": {}},
        },
    ]

    server = MCPServer("homelab-ops", port, tools)

    server.register_handler("docker_status", docker_status)
    server.register_handler("docker_container_logs", docker_container_logs)
    server.register_handler("docker_container_restart", docker_container_restart)
    server.register_handler("docker_container_stop", docker_container_stop)
    server.register_handler("disk_usage", disk_usage)
    server.register_handler("system_load", system_load)
    server.register_handler("homelab_health", homelab_health)
    server.register_handler("container_health", container_health)

    log.info("homelab-ops MCP server starting on port %d", port)
    server.start()


if __name__ == "__main__":
    main()
