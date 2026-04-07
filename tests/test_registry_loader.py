"""Tests for registry bundle loader."""
from __future__ import annotations

import textwrap

import pytest
import yaml

from core.registry.loader import load_registry


def _write_yaml(path, data):
    """Helper to write a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))


# ── Bundle loading ───────────────────────────────────────────────


def test_load_bundles(tmp_path) -> None:
    """Core and homelab bundles both get loaded."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    _write_yaml(core_dir / "bundle.yaml", {
        "checks": {
            "cpu_temp": {
                "command": "sensors -u",
                "type": "threshold",
                "warn": 80,
                "critical": 95,
            },
        },
        "tools": {},
        "harnesses": {},
    })

    homelab_dir = tmp_path / "homelab"
    homelab_dir.mkdir()
    _write_yaml(homelab_dir / "bundle.yaml", {
        "checks": {
            "disk_usage": {
                "command": "df -h /",
                "type": "threshold",
                "warn": 85,
                "critical": 95,
            },
        },
        "tools": {},
        "harnesses": {},
    })

    reg = load_registry(tmp_path)
    assert "cpu_temp" in reg["checks"]
    assert "disk_usage" in reg["checks"]
    assert reg["validation_errors"] == []


def test_overrides_win(tmp_path) -> None:
    """User overrides take precedence over bundle values."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    _write_yaml(core_dir / "bundle.yaml", {
        "checks": {
            "disk_usage": {
                "command": "df -h /",
                "type": "threshold",
                "warn": 85,
                "critical": 95,
            },
        },
        "tools": {},
        "harnesses": {},
    })

    overrides_file = tmp_path / "overrides.yaml"
    _write_yaml(overrides_file, {
        "checks": {
            "disk_usage": {
                "command": "df -h /",
                "type": "threshold",
                "warn": 70,
                "critical": 95,
            },
        },
    })

    reg = load_registry(tmp_path, overrides_file=overrides_file)
    assert reg["checks"]["disk_usage"]["warn"] == 70


def test_validation_errors_reported(tmp_path) -> None:
    """Invalid check type produces validation errors."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    _write_yaml(core_dir / "bundle.yaml", {
        "checks": {
            "bad_check": {
                "command": "echo hi",
                "type": "magic",
            },
        },
        "tools": {},
        "harnesses": {},
    })

    reg = load_registry(tmp_path)
    assert len(reg["validation_errors"]) > 0
    assert any("type" in e for e in reg["validation_errors"])


def test_discovered_yaml_merged(tmp_path) -> None:
    """Runtime-generated discovered.yaml gets merged into the registry."""
    homelab_dir = tmp_path / "homelab"
    homelab_dir.mkdir()
    _write_yaml(homelab_dir / "bundle.yaml", {
        "checks": {},
        "tools": {},
        "harnesses": {},
    })
    _write_yaml(homelab_dir / "discovered.yaml", {
        "checks": {
            "jellyfin_health": {
                "command": "curl -s http://localhost:8096/health",
                "type": "http_probe",
            },
        },
    })

    reg = load_registry(tmp_path)
    assert "jellyfin_health" in reg["checks"]


def test_harnesses_loaded(tmp_path) -> None:
    """Harnesses from bundles are loaded into the registry."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    _write_yaml(core_dir / "bundle.yaml", {
        "checks": {},
        "tools": {},
        "harnesses": {
            "backup": {
                "script": "backup.sh",
                "frequency": "1d",
                "window": "offline",
            },
            "update_check": {
                "script": "update.sh",
                "frequency": "weekly",
                "window": "online",
            },
        },
    })

    reg = load_registry(tmp_path)
    assert "backup" in reg["harnesses"]
    assert "update_check" in reg["harnesses"]
    assert reg["harnesses"]["backup"]["frequency"] == "1d"
