"""Service discovery — Docker containers, LLM servers, listening ports.

Discovers what's running on the host so AgentHarness knows what services
are available. Handles missing tools (Docker, ss) gracefully.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# Ports commonly used by local LLM inference servers
LLM_PORTS = [8080, 8081, 11434, 5000, 8000, 1234]


def _run(cmd: str, timeout: int = 10) -> str | None:
    """Run a shell command safely, returning stdout or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Command failed: %s — %s", cmd, exc)
        return None


def _parse_docker_ps(output: str) -> list[dict]:
    """Parse JSON lines from `docker ps --format json` into container dicts."""
    if not output or not output.strip():
        return []

    containers = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            containers.append({
                "id": raw.get("ID", ""),
                "name": raw.get("Names", ""),
                "image": raw.get("Image", ""),
                "ports": raw.get("Ports", ""),
                "status": raw.get("Status", ""),
            })
        except json.JSONDecodeError:
            logger.debug("Skipping unparseable docker ps line: %s", line)
    return containers


def discover_docker_services() -> list[dict]:
    """Discover running Docker containers. Returns [] if Docker is unavailable."""
    output = _run("docker ps --format json")
    if output is None:
        return []
    return _parse_docker_ps(output)


def discover_llm_servers() -> list[dict]:
    """Probe common LLM ports for /health and /v1/models endpoints."""
    servers = []
    for port in LLM_PORTS:
        info = _probe_llm_port(port)
        if info:
            servers.append(info)
    return servers


def _probe_llm_port(port: int, host: str = "127.0.0.1") -> dict | None:
    """Check if a port hosts an LLM server by probing health/models endpoints."""
    for path in ("/health", "/v1/models"):
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", path)
            resp = conn.getresponse()
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="replace")
                conn.close()
                return {
                    "port": port,
                    "host": host,
                    "endpoint": path,
                    "status": "healthy",
                    "response_preview": body[:200],
                }
            conn.close()
        except (OSError, http.client.HTTPException):
            pass
    return None


def discover_listening_ports() -> dict[int, str]:
    """Discover listening TCP ports. Uses ss on Linux, lsof on macOS."""
    # Try ss first (Linux)
    output = _run("ss -tlnp")
    if output:
        return _parse_ss(output)

    # Fallback to lsof (macOS)
    output = _run("lsof -i -P -n -sTCP:LISTEN")
    if output:
        return _parse_lsof(output)

    return {}


def _parse_ss(output: str) -> dict[int, str]:
    """Parse ss -tlnp output into {port: process_name}."""
    ports = {}
    for line in output.splitlines()[1:]:  # skip header
        # Look for :port pattern and process name
        port_match = re.search(r":(\d+)\s", line)
        proc_match = re.search(r'users:\(\("([^"]+)"', line)
        if port_match:
            port = int(port_match.group(1))
            process = proc_match.group(1) if proc_match else "unknown"
            ports[port] = process
    return ports


def _parse_lsof(output: str) -> dict[int, str]:
    """Parse lsof -i -P -n output into {port: process_name}."""
    ports = {}
    for line in output.splitlines()[1:]:  # skip header
        if "(LISTEN)" not in line:
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        command = parts[0]
        # NAME column is the 9th field (index 8), e.g. "*:8080" or "127.0.0.1:3000"
        name_col = parts[8]
        port_match = re.search(r":(\d+)$", name_col)
        if port_match:
            port = int(port_match.group(1))
            ports[port] = command
    return ports


def discover_services() -> dict:
    """Run all discovery and return a combined dict."""
    return {
        "docker": discover_docker_services(),
        "llm_servers": discover_llm_servers(),
        "listening_ports": discover_listening_ports(),
    }
