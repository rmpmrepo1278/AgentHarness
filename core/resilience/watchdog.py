"""Self-watchdog: heartbeat writing, stale lock recovery, process monitoring.

Provides liveness heartbeats so external monitors (or AgentHarness itself on
restart) can tell whether the agent is alive, and recovers stale lock files
left behind by crashed processes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_HEARTBEAT_FILE = "heartbeat.json"


def write_heartbeat(data_dir: Path | str) -> None:
    """Write heartbeat.json with timestamp, pid, and ISO time."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    hb_path = data_dir / _HEARTBEAT_FILE

    payload: Dict[str, Any] = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "iso": datetime.now(timezone.utc).isoformat(),
    }

    tmp_path = hb_path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.rename(hb_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def check_heartbeat(
    data_dir: Path | str, max_age_seconds: float = 1200
) -> Dict[str, Any]:
    """Check heartbeat freshness.

    Returns a dict with:
        status: "ok" | "stale" | "missing"
        age_seconds: float (only if heartbeat exists)
        pid: int (only if heartbeat exists)
    """
    data_dir = Path(data_dir)
    hb_path = data_dir / _HEARTBEAT_FILE

    if not hb_path.exists():
        return {"status": "missing"}

    try:
        data = json.loads(hb_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot read heartbeat at %s: %s", hb_path, exc)
        return {"status": "missing"}

    age = time.time() - data["timestamp"]
    status = "ok" if age <= max_age_seconds else "stale"

    return {
        "status": status,
        "age_seconds": age,
        "pid": data.get("pid"),
    }


def _pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID exists (signal 0 trick)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it.
        return True


def recover_stale_lock(lock_file: Path | str) -> bool:
    """Read PID from lock file; remove if the process is dead.

    Returns True if the lock was removed, False otherwise.
    """
    lock_file = Path(lock_file)
    if not lock_file.exists():
        return False

    try:
        pid = int(lock_file.read_text().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Cannot parse PID from %s: %s — removing stale lock", lock_file, exc)
        lock_file.unlink(missing_ok=True)
        return True

    if _pid_alive(pid):
        logger.debug("PID %d still alive — keeping lock %s", pid, lock_file)
        return False

    logger.info("PID %d is dead — removing stale lock %s", pid, lock_file)
    lock_file.unlink(missing_ok=True)
    return True


def recover_all_stale_locks(data_dir: Path | str) -> List[str]:
    """Glob *.lock under data_dir, recover each, return list of recovered file names."""
    data_dir = Path(data_dir)
    recovered: List[str] = []
    for lock_file in sorted(data_dir.glob("*.lock")):
        if recover_stale_lock(lock_file):
            recovered.append(lock_file.name)
    return recovered
