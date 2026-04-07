"""Tests for pre-deploy validation checks."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


def test_validate_local():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert "python_version" in result
    assert result["python_version"]["status"] in ("ok", "fail")
    assert "disk_space" in result


def test_validate_local_checks_python():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["python_version"]["status"] == "ok"  # We're running Python


def test_validate_local_checks_disk():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["disk_space"]["status"] == "ok"  # Assume >1GB free


def test_validate_format_report():
    from core.doctor.validate_remote import validate_local, format_report
    result = validate_local()
    report = format_report(result)
    assert isinstance(report, str)
    assert "python" in report.lower()


def test_validate_local_has_all_checks():
    """Ensure all eight required checks are present."""
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    expected_keys = {
        "python_version", "disk_space", "docker", "systemd",
        "git", "home_writable", "pip", "pyyaml",
    }
    assert expected_keys == set(result.keys())


def test_check_status_values():
    """Every check must return 'ok' or 'fail'."""
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    for name, check in result.items():
        assert check["status"] in ("ok", "fail"), f"{name} has invalid status"
        assert "detail" in check, f"{name} missing detail"
        assert "name" in check, f"{name} missing name field"


def test_format_report_counts():
    """Report must include pass/fail counts."""
    from core.doctor.validate_remote import format_report
    fake = {
        "a": {"name": "a", "status": "ok", "detail": "good"},
        "b": {"name": "b", "status": "fail", "detail": "bad"},
    }
    report = format_report(fake)
    assert "1 passed" in report
    assert "1 failed" in report
    assert "Fix the failures" in report


def test_format_report_all_pass():
    """When all pass, report says ready to deploy."""
    from core.doctor.validate_remote import format_report
    fake = {
        "a": {"name": "a", "status": "ok", "detail": "good"},
    }
    report = format_report(fake)
    assert "Ready to deploy" in report


def test_validate_local_git_available():
    """Git should be available in this dev environment."""
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["git"]["status"] == "ok"


def test_validate_local_home_writable():
    """Home directory should be writable."""
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["home_writable"]["status"] == "ok"


def test_validate_local_pip_available():
    """pip should be available via the current interpreter."""
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["pip"]["status"] == "ok"
