"""Network window detection — online/offline/LAN state + frequency parsing."""
from __future__ import annotations

import re
import subprocess
import time


def _ping(host: str = "8.8.8.8", timeout: int = 3) -> bool:
    """Ping a host via subprocess. Returns True if reachable."""
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=timeout + 1,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _ping_host(host: str) -> bool:
    """Ping a specific host. Returns True if reachable."""
    return _ping(host, timeout=2)


def get_network_state(minipc_ip: str = "") -> str:
    """Detect current network state.

    Returns:
        "online"   — internet reachable
        "lan_only" — internet down but LAN peer reachable
        "offline"  — nothing reachable
    """
    if _ping():
        return "online"
    if minipc_ip and _ping_host(minipc_ip):
        return "lan_only"
    return "offline"


def get_window(network_state: str) -> str:
    """Map network state to scheduling window name.

    Returns:
        "online"      — full internet access
        "offline_lan" — LAN-only tasks
        "offline"     — local-only tasks
    """
    mapping = {
        "online": "online",
        "lan_only": "offline_lan",
        "offline": "offline",
    }
    return mapping.get(network_state, "offline")


_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_frequency(freq_str: str) -> int:
    """Parse a human-friendly frequency string into seconds.

    Examples:
        "15m"   → 900
        "6h"    → 21600
        "daily" → 86400
        "3d"    → 259200
    """
    if freq_str == "daily":
        return 86400
    if freq_str == "weekly":
        return 604800
    if freq_str == "monthly":
        return 2592000
    if freq_str == "on_boot":
        return 0

    match = re.fullmatch(r"(\d+)([smhd])", freq_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        return value * _UNIT_SECONDS[unit]

    raise ValueError(f"Unknown frequency format: {freq_str!r}")


def is_task_due(frequency: str, last_run: float) -> bool:
    """Check whether a task is due based on frequency and last run timestamp.

    Args:
        frequency: Human-friendly frequency string (e.g. "daily", "15m").
        last_run: Unix timestamp of last execution.

    Returns:
        True if enough time has elapsed since last_run.
    """
    interval = parse_frequency(frequency)
    elapsed = time.time() - last_run
    return elapsed >= interval
