"""Resource self-monitoring for AgentHarness.

Track own CPU, RAM, and disk footprint so Chaguli can detect runaway
resource usage before it impacts the homelab.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import resource
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from core.resilience.atomic_json import atomic_append_json, safe_read_json

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _get_rss_mb() -> float:
    """Resident Set Size of the current process in MB.

    Try /proc/self/status first (Linux), fall back to resource.getrusage.
    """
    proc_status = Path("/proc/self/status")
    if proc_status.exists():
        try:
            for line in proc_status.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    # Value is in kB
                    return int(line.split()[1]) / 1024.0
        except (OSError, ValueError, IndexError):
            pass

    # Fallback: resource.getrusage (maxrss is in KB on Linux, bytes on macOS)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    maxrss = usage.ru_maxrss
    if platform.system() == "Darwin":
        # macOS reports bytes
        return maxrss / (1024.0 * 1024.0)
    # Linux reports KB
    return maxrss / 1024.0


# Module-level state for CPU percent delta
_last_cpu_times: float = 0.0
_last_wall_time: float = 0.0


def _get_cpu_percent() -> float:
    """CPU usage percent since last call (user + system time).

    Uses os.times() delta. First call returns 0.0.
    """
    global _last_cpu_times, _last_wall_time

    t = os.times()
    current_cpu = t.user + t.system
    current_wall = time.monotonic()

    if _last_wall_time == 0.0:
        _last_cpu_times = current_cpu
        _last_wall_time = current_wall
        return 0.0

    wall_delta = current_wall - _last_wall_time
    cpu_delta = current_cpu - _last_cpu_times

    _last_cpu_times = current_cpu
    _last_wall_time = current_wall

    if wall_delta <= 0:
        return 0.0
    return round((cpu_delta / wall_delta) * 100.0, 2)


def _dir_size_mb(path: Path) -> float:
    """Sum of all file sizes under *path* in MB."""
    if not path.is_dir():
        return 0.0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return round(total / (1024.0 * 1024.0), 4)


def _total_ram_mb() -> float:
    """Total physical RAM in MB."""
    # Try /proc/meminfo (Linux)
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024.0
        except (OSError, ValueError, IndexError):
            pass

    # macOS: sysctl
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return int(out.strip()) / (1024.0 * 1024.0)
        except (subprocess.SubprocessError, ValueError):
            pass

    return 0.0


def _available_ram_mb() -> float:
    """Available RAM in MB."""
    # Try /proc/meminfo (Linux)
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
        except (OSError, ValueError, IndexError):
            pass

    # macOS: vm_stat
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(
                ["vm_stat"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            text = out.decode()
            free_pages = 0
            for line in text.splitlines():
                if "Pages free:" in line or "Pages inactive:" in line:
                    val = line.split(":")[1].strip().rstrip(".")
                    free_pages += int(val)
            # Page size is typically 16384 on Apple Silicon, 4096 on Intel
            page_size = 16384 if platform.machine() == "arm64" else 4096
            return (free_pages * page_size) / (1024.0 * 1024.0)
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass

    return 0.0


def _disk_free_gb(path: str) -> float:
    """Free disk space at *path* in GB."""
    try:
        usage = shutil.disk_usage(path)
        return round(usage.free / (1024.0 ** 3), 2)
    except OSError:
        return 0.0


# ── ResourceMonitor ──────────────────────────────────────────────────


class ResourceMonitor:
    """Track AgentHarness's own CPU, RAM, and disk footprint."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.history_file = self.data_dir / "resource_usage.json"

    def snapshot(self) -> Dict[str, Any]:
        """Take a resource usage snapshot right now."""
        return {
            "timestamp": time.time(),
            "process": {
                "pid": os.getpid(),
                "rss_mb": _get_rss_mb(),
                "cpu_percent": _get_cpu_percent(),
            },
            "data_dir": {
                "total_mb": _dir_size_mb(self.data_dir),
                "logs_mb": _dir_size_mb(self.data_dir / "logs"),
                "reports_mb": _dir_size_mb(self.data_dir / "reports"),
                "briefings_mb": _dir_size_mb(self.data_dir / "briefings"),
                "proposals_mb": _dir_size_mb(self.data_dir / "proposals"),
            },
            "system": {
                "total_ram_mb": _total_ram_mb(),
                "available_ram_mb": _available_ram_mb(),
                "disk_free_gb": _disk_free_gb("/"),
            },
        }

    def record(self) -> None:
        """Take snapshot and append to resource_usage.json."""
        snap = self.snapshot()
        atomic_append_json(self.history_file, snap)

    def summary(self, hours: int = 24) -> Dict[str, Any]:
        """Summarize resource usage over the last N hours."""
        history: List[Dict[str, Any]] = safe_read_json(self.history_file, default=[])
        if not isinstance(history, list):
            history = []

        cutoff = time.time() - (hours * 3600)
        recent = [s for s in history if s.get("timestamp", 0) >= cutoff]

        if not recent:
            return {
                "hours": hours,
                "snapshots": 0,
                "avg_rss_mb": 0.0,
                "max_rss_mb": 0.0,
                "avg_cpu_percent": 0.0,
                "max_cpu_percent": 0.0,
                "data_dir_total_mb": 0.0,
            }

        rss_vals = [s["process"]["rss_mb"] for s in recent if "process" in s]
        cpu_vals = [s["process"]["cpu_percent"] for s in recent if "process" in s]
        data_totals = [
            s["data_dir"]["total_mb"] for s in recent if "data_dir" in s
        ]

        def _avg(vals: List[float]) -> float:
            return round(sum(vals) / len(vals), 2) if vals else 0.0

        def _max(vals: List[float]) -> float:
            return round(max(vals), 2) if vals else 0.0

        return {
            "hours": hours,
            "snapshots": len(recent),
            "avg_rss_mb": _avg(rss_vals),
            "max_rss_mb": _max(rss_vals),
            "avg_cpu_percent": _avg(cpu_vals),
            "max_cpu_percent": _max(cpu_vals),
            "data_dir_total_mb": _max(data_totals),
        }

    def format_report(self) -> str:
        """Human-readable resource report."""
        snap = self.snapshot()
        summary = self.summary(hours=24)

        lines = [
            "=== AgentHarness Resource Report ===",
            "",
            "-- Process --",
            f"  PID          : {snap['process']['pid']}",
            f"  RSS (now)    : {snap['process']['rss_mb']:.1f} MB",
            f"  CPU (now)    : {snap['process']['cpu_percent']:.1f}%",
            "",
            "-- Data Directory --",
            f"  Total        : {snap['data_dir']['total_mb']:.2f} MB",
            f"  Logs         : {snap['data_dir']['logs_mb']:.2f} MB",
            f"  Reports      : {snap['data_dir']['reports_mb']:.2f} MB",
            f"  Briefings    : {snap['data_dir']['briefings_mb']:.2f} MB",
            f"  Proposals    : {snap['data_dir']['proposals_mb']:.2f} MB",
            "",
            "-- System --",
            f"  Total RAM    : {snap['system']['total_ram_mb']:.0f} MB",
            f"  Available RAM: {snap['system']['available_ram_mb']:.0f} MB",
            f"  Disk free    : {snap['system']['disk_free_gb']:.1f} GB",
        ]

        if summary["snapshots"] > 0:
            lines.extend([
                "",
                f"-- 24h Summary ({summary['snapshots']} snapshots) --",
                f"  Avg RSS      : {summary['avg_rss_mb']:.1f} MB",
                f"  Max RSS      : {summary['max_rss_mb']:.1f} MB",
                f"  Avg CPU      : {summary['avg_cpu_percent']:.1f}%",
                f"  Max CPU      : {summary['max_cpu_percent']:.1f}%",
                f"  Data dir     : {summary['data_dir_total_mb']:.2f} MB",
            ])

        return "\n".join(lines)
