"""Tests for core.discovery.engine — discovery engine coordinator."""
from __future__ import annotations

import os

import pytest

from core.discovery.engine import run_discovery
from core.discovery.state import StateManager


@pytest.fixture
def discovery_env(tmp_path, monkeypatch):
    """Create tmp dirs with scripts/ and config/ subdirs, set env vars."""
    install_dir = tmp_path / "agent"
    install_dir.mkdir()
    (install_dir / "scripts").mkdir()
    (install_dir / "config").mkdir()

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Clean env first
    for key in list(os.environ):
        if key.startswith("AH_") or key.startswith("AGENTHARNESS_"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("AGENTHARNESS_HOME", str(install_dir))
    monkeypatch.setenv("AH_DATA_DIR", str(data_dir))

    return {"install_dir": install_dir, "data_dir": data_dir}


class TestDiscoveryEngine:
    def test_full_discovery(self, discovery_env):
        """run_discovery returns dict with paths, hardware, services keys;
        paths.install_dir matches env."""
        result = run_discovery()

        assert "paths" in result
        assert "hardware" in result
        assert "services" in result
        assert result["paths"]["install_dir"] == str(discovery_env["install_dir"])

    def test_full_discovery_writes_state(self, discovery_env):
        """After discovery, StateManager can read back the state."""
        run_discovery()

        sm = StateManager(data_dir=str(discovery_env["data_dir"]))
        state = sm.read()

        assert "paths" in state
        assert "hardware" in state
        assert "services" in state
        assert state["schema_version"] == 1
