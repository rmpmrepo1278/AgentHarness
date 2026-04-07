# tests/test_optimize_scout.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def scout_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Write hardware info
    state = {"hardware": {"total_ram_gb": 36, "cpu_model": "Ryzen 4700U", "has_amd_gpu": False, "has_npu": False}}
    (data_dir / "state.json").write_text(json.dumps(state))
    return data_dir


def test_scout_returns_findings(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    # Mock the HTTP calls
    with patch("core.optimize.scout.httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"tag_name": "v1.0.0", "name": "New release", "body": "Faster inference", "html_url": "https://github.com/test/repo/releases/tag/v1.0.0"}
        ]
        mock_get.return_value = mock_resp
        findings = s.search_github_releases(["test/repo"])
    assert len(findings) > 0
    assert "tag" in findings[0]


def test_scout_handles_network_error(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    with patch("core.optimize.scout.httpx.get", side_effect=Exception("Network down")):
        findings = s.search_github_releases(["test/repo"])
    assert findings == []


def test_scout_search_all(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    with patch.object(s, "search_github_releases", return_value=[]):
        with patch.object(s, "search_huggingface", return_value=[]):
            results = s.search_all()
    assert isinstance(results, list)
