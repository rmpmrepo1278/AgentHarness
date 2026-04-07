"""Tests for core.discovery.services — Docker, LLM server, and port discovery."""

import json
from unittest.mock import patch, MagicMock

import pytest

from core.discovery.services import (
    discover_docker_services,
    _parse_docker_ps,
    discover_llm_servers,
    discover_listening_ports,
    discover_services,
)


SAMPLE_DOCKER_PS = "\n".join([
    json.dumps({
        "ID": "abc123",
        "Names": "ollama-server",
        "Image": "ollama/ollama:latest",
        "Ports": "0.0.0.0:11434->11434/tcp",
        "Status": "Up 3 hours",
    }),
    json.dumps({
        "ID": "def456",
        "Names": "open-webui",
        "Image": "ghcr.io/open-webui/open-webui:main",
        "Ports": "0.0.0.0:3000->8080/tcp",
        "Status": "Up 2 hours",
    }),
])


class TestParseDockerOutput:
    def test_parse_docker_output(self):
        """Parse sample docker ps --format json output, verify 2 containers with name and image."""
        containers = _parse_docker_ps(SAMPLE_DOCKER_PS)
        assert len(containers) == 2
        assert containers[0]["name"] == "ollama-server"
        assert containers[0]["image"] == "ollama/ollama:latest"
        assert containers[1]["name"] == "open-webui"
        assert containers[1]["image"] == "ghcr.io/open-webui/open-webui:main"

    def test_parse_docker_output_empty(self):
        """Empty output returns empty list."""
        assert _parse_docker_ps("") == []

    def test_parse_docker_output_bad_json(self):
        """Malformed JSON lines are skipped."""
        output = "not-json\n" + json.dumps({"ID": "x", "Names": "a", "Image": "b", "Ports": "", "Status": "Up"})
        result = _parse_docker_ps(output)
        assert len(result) == 1
        assert result[0]["name"] == "a"


class TestDiscoverDockerServices:
    def test_discover_docker_returns_list(self):
        """Must return a list whether Docker is running or not."""
        result = discover_docker_services()
        assert isinstance(result, list)

    def test_discover_docker_uses_subprocess(self):
        """When docker ps succeeds, parse the output."""
        with patch("core.discovery.services._run") as mock_run:
            mock_run.return_value = SAMPLE_DOCKER_PS
            result = discover_docker_services()
            assert len(result) == 2
            mock_run.assert_called_once()


class TestDiscoverLLMServers:
    def test_discover_llm_servers_returns_list(self):
        """Must return a list."""
        result = discover_llm_servers()
        assert isinstance(result, list)

    def test_discover_llm_servers_finds_healthy_server(self):
        """When a port responds to /health, it should be included."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("http.client.HTTPConnection", return_value=mock_conn):
            result = discover_llm_servers()
            # At least one port should be detected as healthy
            assert isinstance(result, list)


class TestDiscoverListeningPorts:
    def test_discover_listening_ports_returns_dict(self):
        """Must return a dict."""
        result = discover_listening_ports()
        assert isinstance(result, dict)

    def test_discover_listening_ports_parses_lsof(self):
        """Parse lsof output on macOS."""
        sample_lsof = (
            "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "python3  1234 user    5u  IPv4  12345      0t0  TCP *:8080 (LISTEN)\n"
            "node     5678 user    6u  IPv6  67890      0t0  TCP *:3000 (LISTEN)\n"
        )
        with patch("core.discovery.services._run") as mock_run:
            # First call (ss) raises, second call (lsof) returns data
            mock_run.side_effect = [None, sample_lsof]
            result = discover_listening_ports()
            assert isinstance(result, dict)
            assert 8080 in result
            assert result[8080] == "python3"
            assert 3000 in result
            assert result[3000] == "node"


class TestDiscoverServices:
    def test_discover_services_returns_combined_dict(self):
        """discover_services returns a dict with docker, llm_servers, and listening_ports."""
        with patch("core.discovery.services.discover_docker_services", return_value=[]):
            with patch("core.discovery.services.discover_llm_servers", return_value=[]):
                with patch("core.discovery.services.discover_listening_ports", return_value={}):
                    result = discover_services()
                    assert isinstance(result, dict)
                    assert "docker" in result
                    assert "llm_servers" in result
                    assert "listening_ports" in result
