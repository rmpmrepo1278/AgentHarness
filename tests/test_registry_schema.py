"""Tests for registry schema validation."""
from __future__ import annotations

import pytest

from core.registry.schema import validate_check, validate_harness, validate_tool


# ── Check validation ──────────────────────────────────────────────


def test_valid_check_passes() -> None:
    check = {
        "command": "sensors -u",
        "type": "threshold",
        "warn": 70,
        "critical": 90,
        "unit": "°C",
        "message": "CPU temperature",
    }
    assert validate_check("cpu_temp", check) == []


def test_check_missing_command_fails() -> None:
    check = {
        "type": "threshold",
        "warn": 70,
        "critical": 90,
    }
    errors = validate_check("cpu_temp", check)
    assert any("command" in e for e in errors)


def test_check_invalid_type_fails() -> None:
    check = {
        "command": "echo hi",
        "type": "magic",
    }
    errors = validate_check("bogus", check)
    assert any("type" in e for e in errors)


# ── Tool validation ───────────────────────────────────────────────


def test_valid_tool_passes() -> None:
    tool = {
        "description": "Restart nginx",
        "script": "systemctl restart nginx",
        "approval_tier": "approve",
        "sandbox_mode": "direct",
    }
    assert validate_tool("restart_nginx", tool) == []


def test_tool_missing_description_fails() -> None:
    tool = {
        "script": "echo hi",
        "approval_tier": "auto",
        "sandbox_mode": "direct",
    }
    errors = validate_tool("no_desc", tool)
    assert any("description" in e for e in errors)


# ── Harness validation ────────────────────────────────────────────


def test_valid_harness_passes() -> None:
    harness = {
        "script": "backup.sh",
        "frequency": "3d",
        "window": "offline",
    }
    assert validate_harness("backup", harness) == []


def test_harness_invalid_frequency_fails() -> None:
    harness = {
        "script": "backup.sh",
        "frequency": "never",
        "window": "any",
    }
    errors = validate_harness("backup", harness)
    assert any("frequency" in e for e in errors)
