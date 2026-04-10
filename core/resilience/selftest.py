"""Startup self-test — validates state, dirs, Python, Docker, and stale locks.

Run on every boot / scheduler start to catch configuration problems early.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional


def _check(
    name: str,
    fn: Callable[[], None],
    required: bool,
) -> Dict[str, Any]:
    """Run *fn*; return a check-result dict.  Catches all exceptions."""
    try:
        fn()
        return {"name": name, "status": "ok", "required": required}
    except Exception as exc:  # noqa: BLE001
        result: Dict[str, Any] = {
            "name": name,
            "status": "fail",
            "required": required,
            "error": str(exc),
        }
        return result


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a running process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive.
        return True
    return True


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------

def _check_state_file(data_dir: str) -> None:
    path = os.path.join(data_dir, "state.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"state.json missing at {path}")
    with open(path, "r") as fh:
        json.load(fh)  # must be valid JSON


def _check_dir_writable(label: str, path: str) -> None:
    if not os.path.isdir(path):
        raise NotADirectoryError(f"{label}: directory does not exist: {path}")
    # Attempt to create and remove a temp file.
    try:
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".selftest_")
        os.close(fd)
        os.unlink(tmp)
    except OSError as exc:
        raise PermissionError(f"{label}: not writable: {exc}") from exc


def _check_python_version() -> None:
    if sys.version_info < (3, 9):
        raise RuntimeError(
            f"Python >= 3.9 required, running {sys.version_info.major}."
            f"{sys.version_info.minor}"
        )


def _check_docker() -> None:
    subprocess.run(
        ["docker", "info"],
        check=True,
        capture_output=True,
        timeout=5,
    )


def _check_stale_locks(data_dir: str) -> None:
    lock_files = glob.glob(os.path.join(data_dir, "*.lock"))
    stale: List[str] = []
    for lock_path in lock_files:
        try:
            with open(lock_path, "r") as fh:
                content = fh.read().strip()
            pid = int(content)
        except (ValueError, OSError):
            # Can't read / parse — treat as stale.
            stale.append(lock_path)
            continue
        if not _pid_alive(pid):
            stale.append(lock_path)
    if stale:
        raise RuntimeError(f"Stale lock files: {stale}")


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_selftest(data_dir: str) -> Dict[str, Any]:
    """Run all startup checks and return a summary dict.

    Returns::

        {
            "overall": "ok" | "degraded" | "fail",
            "checks": [
                {"name": str, "status": "ok"|"fail"|"skip",
                 "required": bool, "error"?: str},
                ...
            ],
        }
    """
    checks: List[Dict[str, Any]] = []

    # 1. state.json readable
    checks.append(_check("state_file", lambda: _check_state_file(data_dir), required=True))

    # Read state.json for dir paths (best-effort)
    state: Dict[str, str] = {}
    state_path = os.path.join(data_dir, "state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r") as fh:
                state = json.load(fh)
        except Exception:  # noqa: BLE001
            pass

    # 2. reports_dir writable
    reports_dir = state.get("paths", {}).get("reports_dir", "") if isinstance(state.get("paths"), dict) else state.get("reports_dir", "")
    checks.append(
        _check(
            "reports_dir_writable",
            lambda: _check_dir_writable("reports_dir", reports_dir),
            required=True,
        )
    )

    # 3. logs_dir writable
    logs_dir = state.get("paths", {}).get("logs_dir", "") if isinstance(state.get("paths"), dict) else state.get("logs_dir", "")
    checks.append(
        _check(
            "logs_dir_writable",
            lambda: _check_dir_writable("logs_dir", logs_dir),
            required=True,
        )
    )

    # 4. Python version >= 3.9
    checks.append(_check("python_version", _check_python_version, required=True))

    # 5. Docker available (optional)
    checks.append(_check("docker_available", _check_docker, required=False))

    # 6. No stale locks
    checks.append(
        _check("no_stale_locks", lambda: _check_stale_locks(data_dir), required=False)
    )

    # Compute overall
    has_required_fail = any(
        c["status"] == "fail" and c["required"] for c in checks
    )
    has_optional_fail = any(
        c["status"] == "fail" and not c["required"] for c in checks
    )

    if has_required_fail:
        overall = "fail"
    elif has_optional_fail:
        overall = "degraded"
    else:
        overall = "ok"

    return {"overall": overall, "checks": checks}
