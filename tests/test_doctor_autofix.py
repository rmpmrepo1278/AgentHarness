from __future__ import annotations
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def autofix_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "proposals").mkdir()
    selftest = {
        "overall": "fail",
        "checks": [{"name": "reports_dir_writable", "status": "fail", "required": True, "error": "Permission denied"}],
    }
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))
    (data_dir / "state.json").write_text(json.dumps({
        "schema_version": 1,
        "paths": {"install_dir": str(tmp_path), "data_dir": str(data_dir), "logs_dir": str(data_dir / "logs")},
        "hardware": {"total_ram_gb": 36},
    }))
    return data_dir


def test_autofix_generates_proposal(autofix_env):
    from core.doctor.autofix import AutoFixer
    from core.providers.base import LLMResponse

    mock_response = LLMResponse(
        text="Root cause: reports directory has wrong permissions.\nFix: sudo chown $USER:$USER /opt/agentharness/data/reports\nVerify: ls -la /opt/agentharness/data/reports",
        provider="groq", model="llama-3.3-70b-versatile", success=True, tokens_in=100, tokens_out=50,
    )

    af = AutoFixer(data_dir=str(autofix_env))
    with patch.object(af, "_call_llm", return_value=mock_response):
        result = af.diagnose_and_propose()

    assert result["success"] is True
    assert "diagnosis" in result
    assert len(result["diagnosis"]) > 0


def test_autofix_handles_llm_failure(autofix_env):
    from core.doctor.autofix import AutoFixer
    from core.providers.base import LLMResponse

    mock_response = LLMResponse.error("router", "All providers exhausted")

    af = AutoFixer(data_dir=str(autofix_env))
    with patch.object(af, "_call_llm", return_value=mock_response):
        result = af.diagnose_and_propose()

    assert result["success"] is False
    assert "error" in result


def test_autofix_no_issues_found(tmp_path):
    from core.doctor.autofix import AutoFixer
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "proposals").mkdir()
    selftest = {"overall": "ok", "checks": [{"name": "state_file", "status": "ok", "required": True}]}
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))
    (data_dir / "state.json").write_text(json.dumps({"schema_version": 1, "paths": {"data_dir": str(data_dir), "logs_dir": str(data_dir / "logs")}, "hardware": {}}))

    af = AutoFixer(data_dir=str(data_dir))
    result = af.diagnose_and_propose()
    assert result["success"] is True
    assert result.get("diagnosis") == "No issues detected"
