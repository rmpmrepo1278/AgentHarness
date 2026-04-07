"""Tests for core.scheduler.scheduler — tick, checks, heartbeat."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.scheduler.scheduler import Scheduler


@pytest.fixture()
def sched_env(tmp_path, monkeypatch):
    """Set up a minimal scheduler environment with mock bundle + state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # bundles/core with bundle.yaml
    bundles_dir = data_dir / "bundles" / "core"
    bundles_dir.mkdir(parents=True)

    bundle_yaml = bundles_dir / "bundle.yaml"
    bundle_yaml.write_text(
        "checks:\n"
        "  test_check:\n"
        "    command: echo 42\n"
        "    type: threshold\n"
        "    warn: 80\n"
        "harnesses:\n"
        "  test_harness:\n"
        "    script: test_harness.sh\n"
        "    frequency: 1h\n"
        "    window: any\n"
    )

    # scripts dir with test_harness.sh
    scripts_dir = data_dir / "scripts"
    scripts_dir.mkdir()
    harness_script = scripts_dir / "test_harness.sh"
    harness_script.write_text("#!/usr/bin/env bash\necho done\n")
    harness_script.chmod(0o755)

    # state.json with paths
    state_file = data_dir / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paths": {
                    "bundles_dir": str(data_dir / "bundles"),
                    "scripts_dir": str(scripts_dir),
                },
            }
        )
    )

    # logs and reports dirs
    (data_dir / "logs").mkdir()
    (data_dir / "reports").mkdir()

    # Set env var
    monkeypatch.setenv("AH_DATA_DIR", str(data_dir))

    return data_dir


def test_scheduler_tick(sched_env, monkeypatch):
    """Tick returns summary with checks_run and correct window."""
    monkeypatch.setattr(
        "core.scheduler.scheduler.get_network_state", lambda *a, **kw: "online"
    )

    scheduler = Scheduler(str(sched_env))
    result = scheduler.tick()

    assert "checks_run" in result
    assert result["window"] == "online"


def test_scheduler_runs_checks(sched_env, monkeypatch):
    """Tick runs at least one check from the bundle."""
    monkeypatch.setattr(
        "core.scheduler.scheduler.get_network_state", lambda *a, **kw: "online"
    )

    scheduler = Scheduler(str(sched_env))
    result = scheduler.tick()

    assert result["checks_run"] > 0


def test_scheduler_writes_heartbeat(sched_env, monkeypatch):
    """Tick writes heartbeat.json into the data directory."""
    monkeypatch.setattr(
        "core.scheduler.scheduler.get_network_state", lambda *a, **kw: "online"
    )

    scheduler = Scheduler(str(sched_env))
    scheduler.tick()

    heartbeat_path = sched_env / "heartbeat.json"
    assert heartbeat_path.exists()

    data = json.loads(heartbeat_path.read_text())
    assert "timestamp" in data
    assert "pid" in data
