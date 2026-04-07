"""Discover agent installations — Chaguli in Docker, OpenClaw on host.

Scans running Docker containers for Chaguli marker files and checks the
local filesystem for an OpenClaw workspace installation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Chaguli marker files — if any of these exist in a mount source, it's Chaguli
_CHAGULI_MARKERS = ("tools.py", "config.yml", "memory.py", "agent.py")

# Chaguli capability modules to detect
_CHAGULI_MODULES = (
    "tools", "memory", "self_improve", "heartbeat",
    "briefings", "agent_loop", "config",
)

# OpenClaw default location
_OPENCLAW_DIR = Path.home() / ".openclaw"


def _run(cmd: str, timeout: int = 10) -> str:
    """Run a shell command and return stdout. Raises on failure."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    result.check_returncode()
    return result.stdout.strip()


def discover_agents() -> list[dict]:
    """Find all agents on this machine.

    Returns:
        List of agent dicts with keys: name, type, path, and agent-specific
        metadata. May be empty if no agents are found.
    """
    agents = []

    # Check Docker containers for Chaguli
    try:
        output = _run("docker ps --format '{{.Names}}'")
        for line in output.splitlines():
            container = line.strip()
            if not container:
                continue
            info = _detect_chaguli_in_container(container)
            if info is not None:
                agents.append(info)
    except Exception:
        pass  # Docker not available or no containers

    # Check for OpenClaw on host
    openclaw = _detect_openclaw()
    if openclaw is not None:
        agents.append(openclaw)

    return agents


def _detect_chaguli_in_container(container_name: str) -> dict | None:
    """Inspect a Docker container for Chaguli marker files.

    Args:
        container_name: Name of the Docker container to inspect.

    Returns:
        Agent dict if Chaguli is detected, None otherwise.
    """
    try:
        raw = _run(f"docker inspect {container_name}")
        info = json.loads(raw)
    except Exception:
        return None

    if not info or not isinstance(info, list):
        return None

    mounts = info[0].get("Mounts", [])
    for mount in mounts:
        source = mount.get("Source", "")
        if not source:
            continue
        host_dir = Path(source)
        # Check if any Chaguli marker files exist in this mount source
        has_marker = any(
            Path(host_dir / marker).exists() for marker in _CHAGULI_MARKERS
        )
        if has_marker:
            capabilities = _detect_chaguli_capabilities(host_dir)
            return {
                "name": "chaguli",
                "type": "container",
                "container": container_name,
                "host_dir": str(host_dir),
                "mount_dest": mount.get("Destination", ""),
                "capabilities": capabilities,
            }

    return None


def _detect_chaguli_capabilities(host_dir: Path) -> list[str]:
    """Check which Chaguli capability modules exist in a directory.

    Args:
        host_dir: Path to the Chaguli source directory on the host.

    Returns:
        Sorted list of detected module names.
    """
    found = []
    for module in _CHAGULI_MODULES:
        if (host_dir / f"{module}.py").exists():
            found.append(module)
    return sorted(found)


def _detect_openclaw() -> dict | None:
    """Check if OpenClaw is installed on the host.

    Looks for ~/.openclaw/workspace directory.

    Returns:
        Agent dict if OpenClaw is found, None otherwise.
    """
    workspace = _OPENCLAW_DIR / "workspace"
    if workspace.is_dir():
        return {
            "name": "openclaw",
            "type": "host",
            "path": str(_OPENCLAW_DIR),
            "workspace": str(workspace),
        }
    return None
