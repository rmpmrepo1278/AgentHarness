"""Tests for core.doctor.engine — RunbookExecutor."""
from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from core.doctor.engine import RunbookExecutor, StepResult, RunbookResult, reset_cooldown
from core.resilience.atomic_json import atomic_write_json


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture()
def runbooks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runbooks"
    d.mkdir()
    return d


@pytest.fixture()
def executor(data_dir: Path, runbooks_dir: Path) -> RunbookExecutor:
    return RunbookExecutor(
        data_dir=str(data_dir),
        runbooks_dir=str(runbooks_dir),
    )


def _write_runbook(runbooks_dir: Path, name: str, data: dict) -> Path:
    """Helper to write a YAML runbook file."""
    p = runbooks_dir / f"{name}.yaml"
    p.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return p


# ------------------------------------------------------------------
# list_runbooks
# ------------------------------------------------------------------


def test_list_runbooks_finds_yaml_files(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "alpha", {
        "name": "alpha",
        "version": 1,
        "trigger": "test",
        "priority": "low",
        "notify": "silent",
        "description": "Alpha runbook",
        "steps": [],
    })
    _write_runbook(runbooks_dir, "beta", {
        "name": "beta",
        "version": 2,
        "trigger": "health",
        "priority": "high",
        "notify": "critical",
        "description": "Beta runbook",
        "steps": [],
    })

    found = executor.list_runbooks()
    names = [r["name"] for r in found]
    assert "alpha" in names
    assert "beta" in names
    assert len(found) == 2

    beta = next(r for r in found if r["name"] == "beta")
    assert beta["version"] == 2
    assert beta["priority"] == "high"


def test_list_runbooks_empty_dir(executor: RunbookExecutor) -> None:
    assert executor.list_runbooks() == []


# ------------------------------------------------------------------
# execute — simple passing runbook
# ------------------------------------------------------------------


def test_execute_simple_passing_runbook(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "echo-test", {
        "name": "echo-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "say-hello", "check": "echo hello", "expect_contains": "hello"},
        ],
    })

    result = executor.execute("echo-test", trigger_context="unit-test")
    assert result.result == "pass"
    assert result.steps_executed == 1
    assert result.steps_passed == 1
    assert result.steps_failed == 0
    assert result.runbook == "echo-test"
    assert result.trigger == "unit-test"
    assert result.duration_seconds >= 0


# ------------------------------------------------------------------
# execute — check fail with fix and verify
# ------------------------------------------------------------------


def test_execute_check_fail_fix_verify(
    executor: RunbookExecutor, runbooks_dir: Path, tmp_path: Path,
) -> None:
    marker = tmp_path / "marker.txt"

    _write_runbook(runbooks_dir, "fix-test", {
        "name": "fix-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-marker",
                "check": f"cat {marker}",
                "expect_contains": "READY",
                "on_fail": [
                    {"name": "create-marker", "fix": f"echo READY > {marker}"},
                    {
                        "name": "verify-marker",
                        "check": f"cat {marker}",
                        "expect_contains": "READY",
                    },
                ],
            },
        ],
    })

    result = executor.execute("fix-test")
    assert result.fix_applied is True

    # The verify step in on_fail should have passed
    verify_step = next(
        (s for s in result.step_results if s.name == "verify-marker"), None
    )
    assert verify_step is not None
    assert verify_step.success is True


# ------------------------------------------------------------------
# execute — with escalation
# ------------------------------------------------------------------


def test_execute_with_escalation(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "escalate-test", {
        "name": "escalate-test",
        "version": 1,
        "trigger": "test",
        "notify": "critical",
        "steps": [
            {
                "name": "always-fail",
                "check": "exit 1",
                "on_fail": [
                    {"escalate": "Service is dead, manual review needed."},
                ],
            },
        ],
    })

    result = executor.execute("escalate-test")
    assert result.result == "escalated"
    escalate_step = next(
        (s for s in result.step_results if s.action == "escalate"), None,
    )
    assert escalate_step is not None
    assert "manual review" in escalate_step.error


# ------------------------------------------------------------------
# dry_run — does not execute fixes
# ------------------------------------------------------------------


def test_dry_run_does_not_execute(
    executor: RunbookExecutor, runbooks_dir: Path, tmp_path: Path,
) -> None:
    marker = tmp_path / "no-touch.txt"

    _write_runbook(runbooks_dir, "dry-test", {
        "name": "dry-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "check-something", "check": "echo ok"},
            {"name": "apply-fix", "fix": f"echo FIXED > {marker}"},
            {"name": "verify", "check": f"cat {marker}", "expect_contains": "FIXED"},
        ],
    })

    results = executor.dry_run("dry-test")
    # All steps should be skipped (dry run)
    assert all(s.skipped for s in results)
    # Marker file should NOT exist
    assert not marker.exists()


# ------------------------------------------------------------------
# lock prevents concurrent execution
# ------------------------------------------------------------------


def test_lock_prevents_concurrent_execution(
    executor: RunbookExecutor, runbooks_dir: Path, data_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "lock-test", {
        "name": "lock-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "ok", "check": "echo ok"},
        ],
    })

    # Manually create a lock with current PID (so it looks alive)
    lock_dir = data_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "lock-test.lock"
    lock_file.write_text(str(os.getpid()))

    result = executor.execute("lock-test")
    # Should fail because the lock is held by a live process
    assert result.result == "fail"
    assert result.steps_executed == 0

    # Clean up lock and verify it can now execute
    lock_file.unlink()
    result2 = executor.execute("lock-test")
    assert result2.result == "pass"


# ------------------------------------------------------------------
# check with expect_contains
# ------------------------------------------------------------------


def test_check_expect_contains(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "contains-test", {
        "name": "contains-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-version",
                "check": "echo 'Python 3.11.4'",
                "expect_contains": "3.11",
            },
        ],
    })

    result = executor.execute("contains-test")
    assert result.result == "pass"
    assert result.steps_passed == 1


def test_check_expect_contains_fails(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "contains-fail", {
        "name": "contains-fail",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-missing",
                "check": "echo 'hello world'",
                "expect_contains": "NOTHERE",
            },
        ],
    })

    result = executor.execute("contains-fail")
    assert result.steps_failed == 1


# ------------------------------------------------------------------
# check with expect_regex
# ------------------------------------------------------------------


def test_check_expect_regex(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "regex-test", {
        "name": "regex-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-usage",
                "check": "echo '42%'",
                "expect_regex": r"^[0-9]+%$",
            },
        ],
    })

    result = executor.execute("regex-test")
    assert result.result == "pass"


def test_check_expect_regex_fails(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "regex-fail", {
        "name": "regex-fail",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-usage-high",
                "check": "echo '95%'",
                "expect_regex": r"^[0-7][0-9]?%$",
            },
        ],
    })

    result = executor.execute("regex-fail")
    assert result.steps_failed == 1


# ------------------------------------------------------------------
# expect_exit_code
# ------------------------------------------------------------------


def test_check_expect_exit_code(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "exit-test", {
        "name": "exit-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {
                "name": "check-exit",
                "check": "exit 0",
                "expect_exit_code": 0,
            },
        ],
    })

    result = executor.execute("exit-test")
    assert result.result == "pass"


# ------------------------------------------------------------------
# nonexistent runbook
# ------------------------------------------------------------------


def test_execute_nonexistent_runbook(executor: RunbookExecutor) -> None:
    result = executor.execute("does-not-exist")
    assert result.result == "fail"
    assert result.steps_executed == 0


# ------------------------------------------------------------------
# log file written
# ------------------------------------------------------------------


def test_execute_writes_log(
    executor: RunbookExecutor, runbooks_dir: Path, data_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "log-test", {
        "name": "log-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "ok", "check": "echo ok"},
        ],
    })

    executor.execute("log-test")

    import json
    log_file = data_dir / "doctor_log.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    # At least one line from the engine log and one from the notifier
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["runbook"] == "log-test"


# ------------------------------------------------------------------
# wait step
# ------------------------------------------------------------------


def test_wait_step(
    executor: RunbookExecutor, runbooks_dir: Path,
) -> None:
    _write_runbook(runbooks_dir, "wait-test", {
        "name": "wait-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "pause", "wait": 1},
            {"name": "after-pause", "check": "echo done"},
        ],
    })

    result = executor.execute("wait-test")
    assert result.result == "pass"
    wait_step = next(s for s in result.step_results if s.name == "pause")
    assert wait_step.action == "wait"
    assert wait_step.success is True


# ------------------------------------------------------------------
# cooldown prevents execution after max attempts
# ------------------------------------------------------------------


def test_cooldown_prevents_execution(
    executor: RunbookExecutor, runbooks_dir: Path, data_dir: Path,
) -> None:
    """After 3 recent attempts, the 4th execution returns result='cooldown'."""
    _write_runbook(runbooks_dir, "cool-test", {
        "name": "cool-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "ok", "check": "echo ok"},
        ],
    })

    # Seed the cooldown file with 3 recent timestamps
    now = time.time()
    cooldowns = {
        "cool-test": {
            "attempts": [now - 60, now - 30, now - 10],
        },
    }
    atomic_write_json(data_dir / "doctor_cooldowns.json", cooldowns)

    result = executor.execute("cool-test", trigger_context="unit-test")
    assert result.result == "cooldown"
    assert result.steps_executed == 0
    assert result.notify_level == "critical"


def test_cooldown_allows_after_window_expires(
    executor: RunbookExecutor, runbooks_dir: Path, data_dir: Path,
) -> None:
    """Old attempts outside the 10-min window do not count."""
    _write_runbook(runbooks_dir, "stale-cool", {
        "name": "stale-cool",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "ok", "check": "echo ok"},
        ],
    })

    # All attempts are older than 10 minutes
    now = time.time()
    cooldowns = {
        "stale-cool": {
            "attempts": [now - 700, now - 800, now - 900],
        },
    }
    atomic_write_json(data_dir / "doctor_cooldowns.json", cooldowns)

    result = executor.execute("stale-cool")
    assert result.result == "pass"


def test_cooldown_records_attempts(
    executor: RunbookExecutor, runbooks_dir: Path, data_dir: Path,
) -> None:
    """Each execution records a timestamp in the cooldowns file."""
    _write_runbook(runbooks_dir, "record-test", {
        "name": "record-test",
        "version": 1,
        "trigger": "test",
        "notify": "silent",
        "steps": [
            {"name": "ok", "check": "echo ok"},
        ],
    })

    executor.execute("record-test")
    executor.execute("record-test")

    cooldowns_path = data_dir / "doctor_cooldowns.json"
    cooldowns = json.loads(cooldowns_path.read_text())
    assert len(cooldowns["record-test"]["attempts"]) == 2


def test_reset_cooldown(
    data_dir: Path,
) -> None:
    """reset_cooldown clears the runbook entry from the cooldowns file."""
    now = time.time()
    cooldowns = {
        "my-rb": {"attempts": [now - 10, now - 5, now - 1]},
        "other-rb": {"attempts": [now - 2]},
    }
    atomic_write_json(data_dir / "doctor_cooldowns.json", cooldowns)

    reset_cooldown(str(data_dir), "my-rb")

    updated = json.loads((data_dir / "doctor_cooldowns.json").read_text())
    assert "my-rb" not in updated
    assert "other-rb" in updated
