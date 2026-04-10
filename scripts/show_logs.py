#!/usr/bin/env python3
"""show_logs.py — Show recent logs for any service, callable from Chaguli.

Usage:
    python3 scripts/show_logs.py SERVICE_NAME [--lines N]

Auto-detects whether SERVICE_NAME is a systemd unit or a Docker container
and fetches logs accordingly. Default: 20 lines.

Output is plain text (no HTML, no markdown) suitable for Telegram.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


# ---------------------------------------------------------------------------
# Known systemd units (extend as new services are added)
# ---------------------------------------------------------------------------
KNOWN_SYSTEMD_UNITS = [
    "llama-primary",
    "agentharness-llm-proxy",
    "agentharness-dashboard",
    "agentharness-inbox-watcher",
]


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a command and return (returncode, combined stdout+stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output = (output + "\n" + result.stderr).strip()
        return result.returncode, output
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, f"Timed out after {timeout}s"


def is_systemd_unit(service: str) -> bool:
    """Return True if the service exists as a systemd unit."""
    rc, _ = run(["systemctl", "cat", service])
    return rc == 0


def is_docker_container(service: str) -> bool:
    """Return True if a Docker container with this name exists (running or stopped)."""
    rc, _ = run(["docker", "inspect", "--type=container", service])
    return rc == 0


def get_systemd_logs(service: str, lines: int) -> str:
    rc, output = run(
        ["journalctl", "-u", service, "--no-pager", "-n", str(lines)],
        timeout=15,
    )
    if rc != 0:
        return f"Failed to read journalctl for {service}: {output}"
    return output


def get_docker_logs(service: str, lines: int) -> str:
    # docker logs writes to stderr for many containers, capture both
    try:
        result = subprocess.run(
            ["docker", "logs", service, "--tail", str(lines)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # docker logs sends app output to stdout and docker-internal to stderr
        output = result.stdout
        if result.stderr:
            output = (output + "\n" + result.stderr).strip()
        if result.returncode != 0 and not output:
            return f"Failed to read docker logs for {service}"
        return output
    except FileNotFoundError:
        return "docker not found on this system"
    except subprocess.TimeoutExpired:
        return f"Timed out reading docker logs for {service}"


def list_available() -> str:
    """Build a summary of known systemd units + running Docker containers."""
    parts = []

    # Systemd units that are actually loaded
    active = []
    for unit in KNOWN_SYSTEMD_UNITS:
        rc, out = run(["systemctl", "is-active", unit])
        active.append(f"  {unit} ({out.strip()})")
    if active:
        parts.append("Systemd services:\n" + "\n".join(active))

    # Running Docker containers
    rc, out = run(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=10,
    )
    if rc == 0 and out.strip():
        names = sorted(out.strip().splitlines())
        parts.append("Docker containers:\n" + "\n".join(f"  {n}" for n in names))

    return "\n\n".join(parts) if parts else "No services detected."


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show recent logs for a service.",
    )
    parser.add_argument("service", nargs="?", help="Service or container name")
    parser.add_argument(
        "--lines", "-n", type=int, default=20, help="Number of lines (default: 20)"
    )
    args = parser.parse_args()

    if not args.service:
        print("Usage: show_logs.py SERVICE_NAME [--lines N]\n")
        print(list_available())
        sys.exit(0)

    service = args.service
    lines = args.lines

    # --- Auto-detect service type ---
    # 1. Check systemd first
    if is_systemd_unit(service):
        print(get_systemd_logs(service, lines))
        return

    # 2. Check Docker
    if is_docker_container(service):
        print(get_docker_logs(service, lines))
        return

    # 3. Not found
    print(f"Service '{service}' not found.\n")
    print(list_available())
    sys.exit(1)


if __name__ == "__main__":
    main()
