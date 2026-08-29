"""Startup self-test — validates state, dirs, Python, Docker, and stale locks.

Run on every boot / scheduler start to catch configuration problems early.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from core.common import fs_checks


def _check(
    name: str,
    fn: Callable[[], None],
    required: bool,
) -> dict[str, Any]:
    """Run *fn*; return a check-result dict.  Catches all exceptions."""
    try:
        fn()
        return {"name": name, "status": "ok", "required": required}
    except Exception as exc:
        result: dict[str, Any] = {
            "name": name,
            "status": "fail",
            "required": required,
            "error": str(exc),
        }
        return result


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------

def _check_state_file(data_dir: str) -> None:
    path = os.path.join(data_dir, "state.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"state.json missing at {path}")
    with open(path) as fh:
        json.load(fh)  # must be valid JSON


def _check_dir_writable(label: str, path: str) -> None:
    try:
        fs_checks.check_dir_writable(path)
    except (NotADirectoryError, PermissionError) as exc:
        raise type(exc)(f"{label}: {exc}") from exc


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
    stale = fs_checks.find_stale_locks(data_dir)
    if stale:
        raise RuntimeError(f"Stale lock files: {stale}")


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def run_selftest(data_dir: str) -> dict[str, Any]:
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
    checks: list[dict[str, Any]] = []

    # 1. state.json readable
    checks.append(_check("state_file", lambda: _check_state_file(data_dir), required=True))

    # Read state.json for dir paths (best-effort)
    state: dict[str, str] = {}
    state_path = os.path.join(data_dir, "state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path) as fh:
                state = json.load(fh)
        except Exception:
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
