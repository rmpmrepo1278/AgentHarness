"""Shared low-level filesystem/process probes used by resilience & doctor checks.

These primitives carry no policy (no "critical vs warning", no fix steps) so the
startup self-test (selftest) and the interactive doctor (troubleshoot) can each
wrap them with their own contract without duplicating the detection logic.
"""
from __future__ import annotations

import glob
import os
import tempfile
from typing import List


def pid_alive(pid: int) -> bool:
    """Return True if *pid* is a running process (signal 0 trick)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True
    return True


def find_stale_locks(data_dir: str) -> List[str]:
    """Return lock files under *data_dir* whose recorded PID is dead or unreadable."""
    stale: List[str] = []
    for lock_path in glob.glob(os.path.join(data_dir, "*.lock")):
        try:
            with open(lock_path, "r") as fh:
                content = fh.read().strip()
            pid = int(content)
        except (ValueError, OSError):
            # Can't read / parse — treat as stale.
            stale.append(lock_path)
            continue
        if not pid_alive(pid):
            stale.append(lock_path)
    return stale


def check_dir_writable(path: str) -> None:
    """Raise OSError/PermissionError if *path* is missing or not writable."""
    if not os.path.isdir(path):
        raise NotADirectoryError(f"directory does not exist: {path}")
    try:
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".fscheck_")
        os.close(fd)
        os.unlink(tmp)
    except OSError as exc:
        raise PermissionError(f"directory not writable: {exc}") from exc
