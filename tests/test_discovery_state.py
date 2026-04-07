"""Tests for core.discovery.state — StateManager with atomic writes and file locking."""

import json
import os
import pathlib

import pytest

from core.discovery.state import StateManager


@pytest.fixture
def state_dir(tmp_path):
    """Provide a temporary directory for StateManager data."""
    return tmp_path / "state_data"


class TestStateManager:
    def test_write_and_read(self, state_dir):
        """Write paths, read them back, verify schema_version."""
        sm = StateManager(data_dir=str(state_dir))
        sm.write({"paths": {"config": "/etc/agentharness", "logs": "/var/log/ah"}})

        state = sm.read()
        assert state["schema_version"] == 1
        assert state["paths"]["config"] == "/etc/agentharness"
        assert state["paths"]["logs"] == "/var/log/ah"

    def test_merge_updates(self, state_dir):
        """Write two updates, verify deep merge works."""
        sm = StateManager(data_dir=str(state_dir))
        sm.write({"paths": {"config": "/etc/agentharness"}})
        sm.write({"paths": {"logs": "/var/log/ah"}})

        state = sm.read()
        assert state["paths"]["config"] == "/etc/agentharness"
        assert state["paths"]["logs"] == "/var/log/ah"

    def test_read_nonexistent_returns_empty(self, state_dir):
        """Read before any write returns {"schema_version": 1}."""
        sm = StateManager(data_dir=str(state_dir))
        state = sm.read()
        assert state == {"schema_version": 1}

    def test_atomic_write_survives_crash(self, state_dir):
        """A .tmp file alongside valid state doesn't corrupt reads."""
        sm = StateManager(data_dir=str(state_dir))
        sm.write({"paths": {"config": "/etc/agentharness"}})

        # Simulate a crash that left a .tmp file with garbage
        tmp_file = state_dir / "state.json.tmp"
        tmp_file.write_text("THIS IS CORRUPT DATA")

        state = sm.read()
        assert state["paths"]["config"] == "/etc/agentharness"
        assert state["schema_version"] == 1

    def test_ensure_fresh_marks_missing_paths(self, state_dir, tmp_path):
        """Paths pointing to nonexistent dirs are flagged."""
        sm = StateManager(data_dir=str(state_dir))
        sm.write({"paths": {"missing_dir": "/nonexistent/path/that/does/not/exist"}})

        stale = sm.ensure_fresh()
        assert "missing_dir" in stale

    def test_ensure_fresh_keeps_valid_paths(self, state_dir, tmp_path):
        """Paths pointing to existing dirs are not flagged."""
        valid_dir = tmp_path / "real_dir"
        valid_dir.mkdir()

        sm = StateManager(data_dir=str(state_dir))
        sm.write({"paths": {"valid_dir": str(valid_dir)}})

        stale = sm.ensure_fresh()
        assert "valid_dir" not in stale
