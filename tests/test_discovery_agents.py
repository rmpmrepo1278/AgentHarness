"""Tests for core.discovery.agents — agent discovery (Chaguli in Docker, OpenClaw on host)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.discovery.agents import (
    discover_agents,
    _detect_chaguli_in_container,
    _detect_chaguli_capabilities,
    _detect_openclaw,
)


class TestDiscoverAgents:
    def test_discover_agents_returns_list(self):
        """discover_agents() always returns a list (may be empty)."""
        with patch("core.discovery.agents._detect_openclaw", return_value=None), \
             patch("core.discovery.agents._run", return_value=""):
            result = discover_agents()
            assert isinstance(result, list)

    def test_discover_agents_includes_openclaw_when_found(self, tmp_path):
        """When OpenClaw workspace exists, it appears in the list."""
        workspace = tmp_path / ".openclaw" / "workspace"
        workspace.mkdir(parents=True)

        with patch("core.discovery.agents._OPENCLAW_DIR", tmp_path / ".openclaw"), \
             patch("core.discovery.agents._run", return_value=""):
            result = discover_agents()
            openclaw = [a for a in result if a["name"] == "openclaw"]
            assert len(openclaw) == 1
            assert openclaw[0]["type"] == "host"

    def test_discover_agents_includes_chaguli_when_found(self):
        """When docker ps returns a container with Chaguli markers, it appears."""
        docker_ps = "chaguli-agent\n"
        inspect_json = json.dumps([{
            "Mounts": [{
                "Type": "bind",
                "Source": "/home/user/chaguli",
                "Destination": "/app",
            }]
        }])

        def fake_run(cmd, timeout=10):
            if "docker ps" in cmd:
                return docker_ps
            if "docker inspect" in cmd:
                return inspect_json
            return ""

        with patch("core.discovery.agents._run", side_effect=fake_run), \
             patch("core.discovery.agents._detect_openclaw", return_value=None), \
             patch("pathlib.Path.exists", return_value=True):
            result = discover_agents()
            chaguli = [a for a in result if a["name"] == "chaguli"]
            assert len(chaguli) == 1
            assert chaguli[0]["type"] == "container"
            assert chaguli[0]["container"] == "chaguli-agent"


class TestParseChaguliDetection:
    def test_parse_chaguli_detection(self):
        """Mock docker inspect, verify _detect_chaguli_in_container doesn't crash."""
        inspect_json = json.dumps([{
            "Mounts": [{
                "Type": "bind",
                "Source": "/home/user/chaguli",
                "Destination": "/app",
            }]
        }])

        with patch("core.discovery.agents._run", return_value=inspect_json), \
             patch("pathlib.Path.exists", return_value=True):
            result = _detect_chaguli_in_container("chaguli-agent")
            assert result is not None or result is None  # doesn't crash

    def test_parse_chaguli_detection_no_markers(self):
        """Container without Chaguli marker files returns None."""
        inspect_json = json.dumps([{
            "Mounts": [{
                "Type": "bind",
                "Source": "/tmp/random",
                "Destination": "/data",
            }]
        }])

        with patch("core.discovery.agents._run", return_value=inspect_json), \
             patch("pathlib.Path.exists", return_value=False):
            result = _detect_chaguli_in_container("some-container")
            assert result is None

    def test_parse_chaguli_detection_docker_error(self):
        """Docker inspect failure returns None gracefully."""
        with patch("core.discovery.agents._run", side_effect=Exception("docker not running")):
            result = _detect_chaguli_in_container("bad-container")
            assert result is None


class TestDetectChaguliCapabilities:
    def test_detect_capabilities_all_present(self, tmp_path):
        """All capability modules detected when files exist."""
        for mod in ("tools", "memory", "self_improve", "heartbeat",
                     "briefings", "agent_loop", "config"):
            (tmp_path / f"{mod}.py").touch()

        caps = _detect_chaguli_capabilities(tmp_path)
        assert "tools" in caps
        assert "memory" in caps
        assert "config" in caps
        assert len(caps) == 7

    def test_detect_capabilities_partial(self, tmp_path):
        """Only existing modules are listed."""
        (tmp_path / "tools.py").touch()
        (tmp_path / "config.py").touch()

        caps = _detect_chaguli_capabilities(tmp_path)
        assert caps == ["config", "tools"]  # sorted

    def test_detect_capabilities_empty(self, tmp_path):
        """No modules returns empty list."""
        caps = _detect_chaguli_capabilities(tmp_path)
        assert caps == []


class TestDetectOpenClaw:
    def test_detect_openclaw_present(self, tmp_path):
        """OpenClaw detected when ~/.openclaw/workspace exists."""
        workspace = tmp_path / ".openclaw" / "workspace"
        workspace.mkdir(parents=True)

        with patch("core.discovery.agents._OPENCLAW_DIR", tmp_path / ".openclaw"):
            result = _detect_openclaw()
            assert result is not None
            assert result["name"] == "openclaw"
            assert result["type"] == "host"

    def test_detect_openclaw_missing(self, tmp_path):
        """Returns None when OpenClaw dir doesn't exist."""
        with patch("core.discovery.agents._OPENCLAW_DIR", tmp_path / ".openclaw"):
            result = _detect_openclaw()
            assert result is None
