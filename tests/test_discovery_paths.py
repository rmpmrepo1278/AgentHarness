"""Tests for core.discovery.paths — path discovery with env var, convention, and override support."""

import os

import pytest

from core.discovery.paths import discover_paths


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all AH_* and AGENTHARNESS_* env vars so tests start clean."""
    for key in list(os.environ):
        if key.startswith("AH_") or key.startswith("AGENTHARNESS_"):
            monkeypatch.delenv(key, raising=False)


class TestDiscoverPaths:
    def test_discover_from_env_var(self, tmp_path, clean_env, monkeypatch):
        """Setting AGENTHARNESS_HOME finds install_dir and scripts_dir."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        (fake_home / "scripts").mkdir()

        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        paths = discover_paths()

        assert paths["install_dir"] == str(fake_home)
        assert paths["scripts_dir"] == str(fake_home / "scripts")

    def test_discover_from_script_location(self, tmp_path, clean_env):
        """Passing hint_dir finds the directory."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()

        paths = discover_paths(hint_dir=str(fake_home))

        assert paths["install_dir"] == str(fake_home)

    def test_discover_creates_missing_data_dirs(self, tmp_path, clean_env, monkeypatch):
        """data_dir, reports_dir, and logs_dir are created if missing."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        paths = discover_paths()

        for key in ("data_dir", "reports_dir", "logs_dir"):
            assert os.path.isdir(paths[key]), f"{key} should be a created directory"

    def test_override_wins(self, tmp_path, clean_env, monkeypatch):
        """Override paths take precedence over discovered paths."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        custom_data = str(tmp_path / "custom_data")
        paths = discover_paths(overrides={"data_dir": custom_data})

        assert paths["data_dir"] == custom_data

    def test_model_dir_discovery(self, tmp_path, clean_env, monkeypatch):
        """model_dir is present in the result."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        paths = discover_paths()

        assert "model_dir" in paths
        assert isinstance(paths["model_dir"], str)

    def test_runtime_error_when_not_found(self, tmp_path, clean_env, monkeypatch):
        """Raises RuntimeError if install dir cannot be resolved."""
        monkeypatch.setenv("HOME", str(tmp_path / "nonexistent_home"))
        # Patch walk-up so it can't find the repo root either
        monkeypatch.setattr("core.discovery.paths._find_by_walking_up", lambda: None)

        with pytest.raises(RuntimeError, match="install"):
            discover_paths()

    def test_data_dir_from_env(self, tmp_path, clean_env, monkeypatch):
        """AH_DATA_DIR env var overrides default data_dir."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        custom_data = str(tmp_path / "env_data")
        monkeypatch.setenv("AH_DATA_DIR", custom_data)

        paths = discover_paths()

        assert paths["data_dir"] == custom_data

    def test_all_derived_paths_present(self, tmp_path, clean_env, monkeypatch):
        """All expected derived path keys are in the result."""
        fake_home = tmp_path / "agent"
        fake_home.mkdir()
        monkeypatch.setenv("AGENTHARNESS_HOME", str(fake_home))

        paths = discover_paths()

        expected_keys = {
            "install_dir", "scripts_dir", "config_dir", "bundles_dir",
            "core_dir", "data_dir", "reports_dir", "logs_dir",
            "proposals_dir", "briefings_dir", "custom_dir", "model_dir",
        }
        assert expected_keys.issubset(paths.keys())
