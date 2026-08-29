#!/usr/bin/env python3
"""System Monitoring - Docker operations."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("docker-ops")

DOCKER_HOST = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")

def _docker(args: list[str], timeout: int = 15) -> dict:
    """Run a docker command."""
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
    """Get overall Docker status."""
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
    """Get container logs."""
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
    result = _docker(["restart", f"--time={timeout}", container], timeout=30)
    return {"container": container, "restarted": result["ok"], "output": result["stdout"] if result["ok"] else result["stderr"]}


def docker_container_stop(args) -> dict:
    """Stop a container."""
    container = args.get("container", "")
    timeout = args.get("timeout", "30")
    if not container:
        return {"error": "container name required"}
    result = _docker(["stop", f"--time={timeout}", container], timeout=60)
    return {"container": container, "stopped": result["ok"], "output": result["stdout"] if result["ok"] else result["stderr"]}


def docker_list_images(args) -> dict:
    """List Docker images."""
    result = _docker(["images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
    images = [i for i in result["stdout"].split("\n") if i.strip()]
    return {"status": "ok", "images": images}


def docker_prune(args) -> dict:
    """Remove unused Docker images."""
    result = _docker(["image", "prune", "-af", "--filter", "until=24h"], timeout=60)
    return {"status": "ok" if result["ok"] else "error", "output": result["stdout"], "error": result["stderr"]}


TOOL_SCHEMAS = [
    {"name": "docker_status", "description": "Get Docker status - running/unhealthy counts, disk usage.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "docker_container_logs", "description": "Get container logs.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "tail": {"type": "string", "default": "50"}}, "required": ["container"]}},
    {"name": "docker_container_restart", "description": "Restart a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "docker_container_stop", "description": "Stop a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "docker_images", "description": "List Docker images.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "docker_prune", "description": "Prune unused Docker images.", "inputSchema": {"type": "object", "properties": {}}},
]


def main():
    import logging
    import os
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("docker-ops")

    port = int(os.environ.get("MCP_PORT", "8103"))
    s = MCPServer(name="system-monitoring-docker", port=port, tools=TOOL_SCHEMAS)

    s.register_handler("docker_status", docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("docker_images", lambda a: {"status": "ok", "images": []})
    s.register_handler("docker_prune", lambda a: {"status": "ok"})

    log.info(f"Docker MCP starting on :{port}")
    s.start()


if __name__ == "__main__":
    main()
