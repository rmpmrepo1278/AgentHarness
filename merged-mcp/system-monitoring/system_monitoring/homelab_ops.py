#!/usr/bin/env python3
"""System Monitoring - Homelab Ops: Docker, systemd, disk, health checks."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homelab-ops-mcp")

DATA_DIR = Path(os.environ.get("HEALTH_DATA_DIR", "/home/rohit/agentharness/data"))
DASHBOARD_FILE = DATA_DIR / "health_dashboard.json"
CONTAINERS_FILE = DATA_DIR / "containers.json"

def _docker(args: list[str], timeout: int = 15) -> dict:
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
        "containers": containers[:30],
    }


def docker_container_logs(args) -> dict:
    container = args.get("container", "")
    tail = args.get("tail", "50")
    if not container:
        return {"error": "container name required"}
    result = _docker(["logs", f"--tail={tail}", container], timeout=10)
    return {"container": container, "logs": result["stdout"][-4000:] if result["ok"] else result["stderr"]}


def docker_container_restart(args) -> dict:
    container = args.get("container", "")
    timeout = args.get("timeout", "30")
    if not container:
        return {"error": "container name required"}
    result = _docker(["restart", f"--time={timeout}", container], timeout=60)
    return {"container": container, "restarted": result["ok"], "output": result["stdout"] if result["ok"] else result["stderr"]}


def docker_container_stop(args) -> dict:
    container = args.get("container", "")
    timeout = args.get("timeout", "30")
    if not container:
        return {"error": "container name required"}
    result = _docker(["stop", f"--time={timeout}", container], timeout=60)
    return {"container": container, "stopped": result["ok"], "output": result["stdout"] if result["ok"] else result["stderr"]}


def disk_usage(args) -> dict:
    mounts = args.get("mounts", "/,/home,/mnt/usb,/var/lib/docker")
    results = []
    for mount in mounts.split(","):
        mount = mount.strip()
        if not mount:
            continue
        try:
            r = subprocess.run(["df", "-h", mount], capture_output=True, text=True, timeout=5)
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

        return {
            "load_1m": float(loads[0]),
            "load_5m": float(loads[1]),
            "load_15m": float(loads[2]),
            "mem_total_mb": total // 1024,
            "mem_available_mb": available // 1024,
            "mem_used_pct": round((total - available) / total * 100, 1) if total else 0,
            "uptime_days": days,
        }
    except Exception as e:
        return {"error": str(e)}


def check_docker_daemon(args) -> dict:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return {"ok": r.returncode == 0, "output": r.stdout[:200] if r.returncode == 0 else r.stderr}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_systemd_services(args) -> dict:
    services = args.get("services", "hermes-gateway,hermes-scheduler,hermes-mind-loop,bmoe-server,health-dashboard,tdai-gateway,opencode-web,loopany")
    results = {}
    for s in services.split(","):
        r = subprocess.run(["systemctl", "--user", "is-active", s], capture_output=True, text=True, timeout=10)
        results[s] = r.stdout.strip()
    return {"services": results}


def container_health(args) -> dict:
    result = _docker(["ps", "--format", "{{.Names}}\t{{.Status}}"])
    if result["returncode"] != 0:
        return {"error": result["stderr"]}

    unhealthy = []
    for line in result["stdout"].split("\n"):
        if "\t" not in line:
            continue
        name, status = line.split("\t", 1)
        if "unhealthy" in status:
            unhealthy.append({"name": name, "status": status})

    return {"unhealthy_count": len(unhealthy), "unhealthy": unhealthy}


def _docker(args: list[str], timeout: int = 15) -> dict:
    try:
        r = subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip(), "returncode": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout", "returncode": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1}


TOOL_SCHEMAS = [
    {"name": "docker_status", "description": "Get Docker status - running/unhealthy counts, disk usage.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "docker_container_logs", "description": "Get container logs.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "tail": {"type": "string", "default": "50"}}, "required": ["container"]}},
    {"name": "docker_container_restart", "description": "Restart a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "docker_container_stop", "description": "Stop a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "disk_usage", "description": "Get disk usage for mount points.",        "inputSchema": {"type": "object", "properties": {"mounts": {"type": "string", "default": "/,/home,/mnt/usb,/var/lib/docker"}}}},
    {"name": "system_load", "description": "Get system load, memory, uptime.",        "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_docker_daemon", "description": "Check if Docker daemon is responsive.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_systemd_services", "description": "Check status of systemd services.", "inputSchema": {"type": "object", "properties": {"services": {"type": "string", "default": "hermes-gateway,hermes-scheduler,hermes-mind-loop,bmoe-server,health-dashboard,tdai-gateway,opencode-web,loopany"}}}},
    {"name": "container_health", "description": "Check health of all containers.",        "inputSchema": {"type": "object", "properties": {}}},
]


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("homelab-ops-mcp")

    import os

    port = int(os.environ.get("MCP_PORT", "8106"))
    s = __import__('mcp_base', fromlist=['MCPServer']).MCPServer(name="system-monitoring-homelab-ops", port=8106, tools=TOOL_SCHEMAS)

    s.register_handler("docker_status", docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("disk_usage", disk_usage)
    s.register_handler("system_load", system_load)
    s.register_handler("check_docker_daemon", check_docker_daemon)
    s.register_handler("check_systemd_services", check_systemd_services)
    s.register_handler("container_health", container_health)

    log.info("Homelab Ops MCP starting on :8106")
    s.start()


if __name__ == "__main__":
    main()
