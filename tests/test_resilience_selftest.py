"""Tests for startup self-test."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.resilience.selftest import run_selftest


@pytest.fixture
def healthy_env(tmp_path: Path) -> Path:
    """Set up a healthy data_dir with state.json and expected sub-dirs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    state = {
        "reports_dir": str(reports_dir),
        "logs_dir": str(logs_dir),
        "scripts_dir": str(scripts_dir),
    }
    (data_dir / "state.json").write_text(json.dumps(state))
    return data_dir


def test_selftest_passes_healthy_system(healthy_env: Path) -> None:
    """Healthy system should report ok or degraded (Docker may be absent)."""
    result = run_selftest(str(healthy_env))
    assert result["overall"] in ("ok", "degraded")
    # All required checks must pass
    for check in result["checks"]:
        if check["required"]:
            assert check["status"] == "ok", f"required check {check['name']} failed"


def test_selftest_detects_missing_state(healthy_env: Path) -> None:
    """Missing state.json should cause state_file check to fail."""
    (healthy_env / "state.json").unlink()
    result = run_selftest(str(healthy_env))
    state_check = next(c for c in result["checks"] if c["name"] == "state_file")
    assert state_check["status"] == "fail"
    assert result["overall"] == "fail"


def test_selftest_detects_unwritable_dir(healthy_env: Path) -> None:
    """Unwritable reports_dir should cause reports_dir_writable check to fail."""
    # Point reports_dir to an impossible path
    state = json.loads((healthy_env / "state.json").read_text())
    state["reports_dir"] = "/nonexistent/impossible/path"
    (healthy_env / "state.json").write_text(json.dumps(state))

    result = run_selftest(str(healthy_env))
    reports_check = next(
        c for c in result["checks"] if c["name"] == "reports_dir_writable"
    )
    assert reports_check["status"] == "fail"
    assert result["overall"] == "fail"


def test_selftest_returns_check_list(healthy_env: Path) -> None:
    """Every check must have name, status, and required fields."""
    result = run_selftest(str(healthy_env))
    assert isinstance(result["checks"], list)
    assert len(result["checks"]) >= 4  # at least state, reports, logs, python
    for check in result["checks"]:
        assert "name" in check
        assert check["status"] in ("ok", "fail", "skip")
        assert "required" in check
