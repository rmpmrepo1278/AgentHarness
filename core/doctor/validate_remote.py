"""Pre-deploy validation — check target machine readiness."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any


def _check(name: str, fn: Any) -> dict[str, Any]:
    """Run a single validation check, catching exceptions."""
    try:
        ok, detail = fn()
        return {"name": name, "status": "ok" if ok else "fail", "detail": detail}
    except Exception as e:
        return {"name": name, "status": "fail", "detail": str(e)}


def validate_local() -> dict[str, dict]:
    """Run validation checks on the local machine.

    Checks: python version >=3.9, disk space >1GB, Docker available,
    systemd available, git available, home writable, pip available,
    pyyaml importable.
    """
    results: dict[str, dict] = {}

    # Python version >= 3.9
    results["python_version"] = _check("python_version", lambda: (
        sys.version_info >= (3, 9),
        "Python %d.%d" % (sys.version_info.major, sys.version_info.minor),
    ))

    # Disk space (need at least 1GB free)
    def _disk_check() -> tuple[bool, str]:
        usage = shutil.disk_usage("/")
        free_gb = usage.free // (1024 ** 3)
        return usage.free > 1_000_000_000, "%dGB free" % free_gb

    results["disk_space"] = _check("disk_space", _disk_check)

    # Docker
    results["docker"] = _check("docker", lambda: (
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        ).returncode == 0,
        "Docker available",
    ))

    # systemd
    results["systemd"] = _check("systemd", lambda: (
        subprocess.run(
            ["systemctl", "--version"], capture_output=True, timeout=5,
        ).returncode == 0,
        "systemd available",
    ))

    # Git
    results["git"] = _check("git", lambda: (
        subprocess.run(
            ["git", "--version"], capture_output=True, timeout=5,
        ).returncode == 0,
        "Git available",
    ))

    # Write permission to home dir
    results["home_writable"] = _check("home_writable", lambda: (
        os.access(os.path.expanduser("~"), os.W_OK),
        os.path.expanduser("~"),
    ))

    # pip available via current interpreter
    results["pip"] = _check("pip", lambda: (
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, timeout=5,
        ).returncode == 0,
        "pip available",
    ))

    # PyYAML importable
    def _pyyaml_check() -> tuple[bool, str]:
        __import__("yaml")
        return True, "PyYAML importable"

    results["pyyaml"] = _check("pyyaml", _pyyaml_check)

    return results


def format_report(results: dict[str, dict]) -> str:
    """Format validation results as a human-readable report."""
    lines = ["Pre-Deploy Validation Report", "=" * 40]
    passed = 0
    failed = 0
    for name, check in results.items():
        status = check.get("status", "?")
        detail = check.get("detail", "")
        icon = "PASS" if status == "ok" else "FAIL"
        lines.append("  [%s] %s: %s" % (icon, name, detail))
        if status == "ok":
            passed += 1
        else:
            failed += 1
    lines.append("")
    lines.append("%d passed, %d failed" % (passed, failed))
    if failed == 0:
        lines.append("Ready to deploy.")
    else:
        lines.append("Fix the failures above before deploying.")
    return "\n".join(lines)
