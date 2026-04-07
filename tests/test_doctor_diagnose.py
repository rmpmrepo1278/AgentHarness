# tests/test_doctor_diagnose.py
from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def doctor_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "reports").mkdir()

    # Create a selftest result with failures
    selftest = {
        "overall": "fail",
        "checks": [
            {"name": "state_file", "status": "ok", "required": True},
            {"name": "docker_available", "status": "fail", "required": False, "error": "docker: command not found"},
            {"name": "reports_dir_writable", "status": "fail", "required": True, "error": "Permission denied: /opt/agentharness/reports"},
        ],
    }
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))

    # Create some error logs
    (data_dir / "logs" / "scheduler.log").write_text(
        "2026-04-07 ERROR: check disk_usage failed: command not found: df\n"
        "2026-04-07 ERROR: harness cleanup timed out after 300s\n"
        "2026-04-07 INFO: tick complete\n" * 100  # Pad with noise
    )

    state = {
        "schema_version": 1,
        "paths": {
            "install_dir": str(tmp_path),
            "data_dir": str(data_dir),
            "logs_dir": str(data_dir / "logs"),
            "scripts_dir": str(tmp_path / "scripts"),
        },
        "hardware": {"total_ram_gb": 36, "cpu_model": "Ryzen 4700U"},
    }
    (data_dir / "state.json").write_text(json.dumps(state))
    return data_dir


def test_collect_context(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    assert "selftest" in context
    assert "errors" in context
    assert "hardware" in context


def test_context_includes_failures(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    assert any("Permission denied" in str(e) for e in context["errors"])


def test_context_is_bounded(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env), max_chars=8000)
    context = dc.collect()
    prompt = dc.format_prompt(context)
    assert len(prompt) <= 8000


def test_format_prompt(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    prompt = dc.format_prompt(context)
    assert "diagnose" in prompt.lower() or "fix" in prompt.lower()
    assert isinstance(prompt, str)
