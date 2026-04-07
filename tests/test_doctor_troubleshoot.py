"""Tests for core.doctor.troubleshoot — guided troubleshooting wizard."""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

from core.doctor.troubleshoot import FixStep, Issue, Troubleshooter


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def healthy_env(tmp_path):
    """Create a fully healthy environment with state.json, dirs, heartbeat, etc."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    state = {
        "reports_dir": str(reports_dir),
        "logs_dir": str(logs_dir),
        "paths": {
            "install_dir": str(tmp_path),
            "data_dir": str(tmp_path),
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state))

    # Write a fresh heartbeat
    heartbeat = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "iso": "2026-04-06T00:00:00+00:00",
    }
    (tmp_path / "heartbeat.json").write_text(json.dumps(heartbeat))

    return tmp_path


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

def test_detects_missing_state(tmp_path):
    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "state_file_missing" in names


def test_generates_fix_steps(tmp_path):
    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    for issue in issues:
        assert len(issue.fix_steps) > 0
        assert all(s.command for s in issue.fix_steps)


def test_format_guide_readable(tmp_path):
    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    guide = t.format_guide(issues)
    assert "Step" in guide or "step" in guide
    assert isinstance(guide, str)


def test_healthy_system_no_issues(healthy_env):
    t = Troubleshooter(data_dir=str(healthy_env))
    issues = t.run()
    critical = [i for i in issues if i.severity == "critical"]
    assert len(critical) == 0


def test_detects_reports_dir_not_writable(tmp_path):
    """When state.json points to a reports_dir that doesn't exist, detect it."""
    state = {
        "reports_dir": str(tmp_path / "nonexistent_reports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "logs").mkdir()

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "reports_dir_not_writable" in names


def test_detects_logs_dir_not_writable(tmp_path):
    """When logs_dir doesn't exist, detect it."""
    state = {
        "reports_dir": str(tmp_path / "reports"),
        "logs_dir": str(tmp_path / "nonexistent_logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "reports").mkdir()

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "logs_dir_not_writable" in names


def test_detects_stale_locks(tmp_path):
    """Stale lock files should be detected."""
    state = {
        "reports_dir": str(tmp_path / "reports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "reports").mkdir()
    (tmp_path / "logs").mkdir()

    # Write a lock file with a dead PID
    (tmp_path / "scheduler.lock").write_text("999999999")

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "stale_locks_detected" in names


def test_detects_circuit_breakers_open(tmp_path):
    """Open circuit breakers should be detected."""
    state = {
        "reports_dir": str(tmp_path / "reports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "reports").mkdir()
    (tmp_path / "logs").mkdir()

    # Write circuit breaker state with high failure counts
    cb_state = {"disk_check": 10, "network_check": 10}
    (tmp_path / "circuit_breaker.json").write_text(json.dumps(cb_state))

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "circuit_breakers_open" in names


def test_detects_stale_heartbeat(tmp_path):
    """A heartbeat older than 30 minutes should be flagged."""
    state = {
        "reports_dir": str(tmp_path / "reports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "reports").mkdir()
    (tmp_path / "logs").mkdir()

    # Write an old heartbeat
    heartbeat = {
        "timestamp": time.time() - 7200,  # 2 hours ago
        "pid": os.getpid(),
    }
    (tmp_path / "heartbeat.json").write_text(json.dumps(heartbeat))

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "scheduler_not_running" in names


def test_no_heartbeat_file_flags_scheduler(tmp_path):
    """Missing heartbeat.json should flag scheduler not running."""
    state = {
        "reports_dir": str(tmp_path / "reports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "reports").mkdir()
    (tmp_path / "logs").mkdir()

    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    names = [i.name for i in issues]
    assert "scheduler_not_running" in names


def test_issue_severity_ordering(tmp_path):
    """Issues should have valid severity levels."""
    t = Troubleshooter(data_dir=str(tmp_path))
    issues = t.run()
    valid_severities = {"critical", "warning", "info"}
    for issue in issues:
        assert issue.severity in valid_severities


def test_format_guide_empty_issues():
    """format_guide with empty list should return healthy message."""
    t = Troubleshooter(data_dir="/nonexistent")
    guide = t.format_guide([])
    assert "healthy" in guide.lower() or "no issues" in guide.lower()


def test_fix_step_dataclass():
    """FixStep fields are accessible."""
    step = FixStep(
        description="Test step",
        command="echo hello",
        verify="echo done",
    )
    assert step.description == "Test step"
    assert step.command == "echo hello"
    assert step.verify == "echo done"


def test_issue_dataclass():
    """Issue fields are accessible."""
    step = FixStep(description="d", command="c", verify="v")
    issue = Issue(
        name="test_issue",
        severity="warning",
        description="A test issue",
        root_cause="Testing",
        fix_steps=[step],
    )
    assert issue.name == "test_issue"
    assert len(issue.fix_steps) == 1
