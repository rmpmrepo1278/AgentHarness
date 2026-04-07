"""Tests for the post-deploy smoketest command."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def smoke_env(tmp_path, monkeypatch):
    """Create a minimal environment for smoketest.

    Provides: data_dir with state.json, bundles/core/bundle.yaml,
    scripts/, logs/, reports/.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    install_dir = tmp_path / "install"
    install_dir.mkdir()

    scripts_dir = install_dir / "scripts"
    scripts_dir.mkdir()

    bundles_dir = install_dir / "bundles"
    core_bundle = bundles_dir / "core"
    core_bundle.mkdir(parents=True)

    logs_dir = data_dir / "logs"
    logs_dir.mkdir()

    reports_dir = data_dir / "reports"
    reports_dir.mkdir()

    # Write a minimal bundle.yaml
    bundle_yaml = core_bundle / "bundle.yaml"
    bundle_yaml.write_text(
        "checks: {}\ntools: {}\nharnesses: {}\n"
    )

    # Write state.json with paths
    state = {
        "paths": {
            "install_dir": str(install_dir),
            "data_dir": str(data_dir),
            "bundles_dir": str(bundles_dir),
            "scripts_dir": str(scripts_dir),
        },
        "logs_dir": str(logs_dir),
        "reports_dir": str(reports_dir),
        "hardware": {},
        "services": {},
        "agents": {},
    }
    state_path = data_dir / "state.json"
    state_path.write_text(json.dumps(state))

    monkeypatch.setenv("AGENTHARNESS_HOME", str(install_dir))
    monkeypatch.setenv("AH_DATA_DIR", str(data_dir))

    return {
        "data_dir": str(data_dir),
        "install_dir": str(install_dir),
        "bundles_dir": str(bundles_dir),
        "scripts_dir": str(scripts_dir),
        "logs_dir": str(logs_dir),
        "reports_dir": str(reports_dir),
    }


def test_smoketest_returns_report(smoke_env, monkeypatch):
    """run_smoketest returns a dict with all expected top-level keys."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest

    result = run_smoketest(smoke_env["data_dir"])
    assert "overall" in result
    assert "discovery" in result
    assert "selftest" in result
    assert "scheduler" in result
    assert "integrity" in result
    assert "bundles" in result
    assert "duration_ms" in result


def test_smoketest_format_report(smoke_env, monkeypatch):
    """format_report produces terminal text with PASS or FAIL."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest, format_report

    result = run_smoketest(smoke_env["data_dir"])
    text = format_report(result)
    assert "PASS" in text or "FAIL" in text


def test_smoketest_measures_duration(smoke_env, monkeypatch):
    """duration_ms must be a positive integer."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest

    result = run_smoketest(smoke_env["data_dir"])
    assert result["duration_ms"] > 0


def test_smoketest_selftest_section(smoke_env, monkeypatch):
    """selftest section has overall, passed, and failed counts."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest

    result = run_smoketest(smoke_env["data_dir"])
    st = result["selftest"]
    assert "overall" in st
    assert "passed" in st
    assert "failed" in st
    assert isinstance(st["passed"], int)
    assert isinstance(st["failed"], int)


def test_smoketest_discovery_section(smoke_env, monkeypatch):
    """discovery section has paths, services, and agents counts."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest

    result = run_smoketest(smoke_env["data_dir"])
    disc = result["discovery"]
    assert "paths" in disc
    assert "services" in disc
    assert "agents" in disc


def test_smoketest_bundles_section(smoke_env, monkeypatch):
    """bundles section has checks, tools, harnesses, and errors counts."""
    monkeypatch.setattr(
        "core.scheduler.windows.get_network_state", lambda *a, **kw: "online"
    )
    from core.doctor.smoketest import run_smoketest

    result = run_smoketest(smoke_env["data_dir"])
    bun = result["bundles"]
    assert "checks" in bun
    assert "tools" in bun
    assert "harnesses" in bun
    assert "errors" in bun
