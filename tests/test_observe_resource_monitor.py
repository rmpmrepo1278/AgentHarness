"""Tests for core.observe.resource_monitor — resource self-monitoring."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def monitor(tmp_path: Path):
    """Create a ResourceMonitor pointed at a temp data dir."""
    from core.observe.resource_monitor import ResourceMonitor

    # Create some subdirs so _dir_size_mb has something to measure
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "briefings").mkdir()
    (tmp_path / "proposals").mkdir()
    # Write a small file so sizes are non-zero
    (tmp_path / "logs" / "test.log").write_text("hello " * 100)
    return ResourceMonitor(data_dir=str(tmp_path))


# ── snapshot ─────────────────────────────────────────────────────────

def test_snapshot_has_required_keys(monitor):
    snap = monitor.snapshot()
    assert "timestamp" in snap
    assert "process" in snap
    assert "data_dir" in snap
    assert "system" in snap


def test_snapshot_process_fields(monitor):
    snap = monitor.snapshot()
    proc = snap["process"]
    assert "pid" in proc
    assert "rss_mb" in proc
    assert "cpu_percent" in proc
    assert proc["rss_mb"] > 0
    assert proc["pid"] > 0


def test_snapshot_data_dir_fields(monitor):
    snap = monitor.snapshot()
    dd = snap["data_dir"]
    assert "total_mb" in dd
    assert "logs_mb" in dd
    assert "reports_mb" in dd
    assert "briefings_mb" in dd
    assert "proposals_mb" in dd
    # total should be >= 0
    assert dd["total_mb"] >= 0


def test_snapshot_system_fields(monitor):
    snap = monitor.snapshot()
    sys_info = snap["system"]
    assert "total_ram_mb" in sys_info
    assert "available_ram_mb" in sys_info
    assert "disk_free_gb" in sys_info
    assert sys_info["total_ram_mb"] > 0
    assert sys_info["available_ram_mb"] > 0
    assert sys_info["disk_free_gb"] > 0


# ── record ───────────────────────────────────────────────────────────

def test_record_appends_to_file(monitor, tmp_path: Path):
    monitor.record()
    monitor.record()

    history = json.loads(monitor.history_file.read_text())
    assert isinstance(history, list)
    assert len(history) == 2
    # Each entry should have a timestamp
    assert "timestamp" in history[0]
    assert "timestamp" in history[1]
    assert history[1]["timestamp"] >= history[0]["timestamp"]


def test_record_creates_history_file(monitor, tmp_path: Path):
    assert not monitor.history_file.exists()
    monitor.record()
    assert monitor.history_file.exists()


# ── summary ──────────────────────────────────────────────────────────

def test_summary_returns_stats(monitor):
    monitor.record()
    monitor.record()
    summary = monitor.summary(hours=1)
    assert "avg_rss_mb" in summary
    assert "max_rss_mb" in summary
    assert "avg_cpu_percent" in summary
    assert "max_cpu_percent" in summary
    assert "data_dir_total_mb" in summary
    assert "snapshots" in summary
    assert summary["snapshots"] == 2


def test_summary_empty_history(monitor):
    summary = monitor.summary(hours=1)
    assert summary["snapshots"] == 0


def test_summary_filters_by_hours(monitor):
    # Record one entry, then manually inject an old one
    monitor.record()
    old_snap = monitor.snapshot()
    old_snap["timestamp"] = time.time() - 3700  # > 1 hour ago
    history = json.loads(monitor.history_file.read_text())
    history.insert(0, old_snap)
    monitor.history_file.write_text(json.dumps(history))

    summary = monitor.summary(hours=1)
    assert summary["snapshots"] == 1  # only the recent one


# ── format_report ────────────────────────────────────────────────────

def test_format_report_returns_string(monitor):
    monitor.record()
    report = monitor.format_report()
    assert isinstance(report, str)
    assert len(report) > 0


def test_format_report_contains_sections(monitor):
    monitor.record()
    report = monitor.format_report()
    assert "Process" in report or "RAM" in report or "MB" in report


def test_format_report_no_history(monitor):
    report = monitor.format_report()
    assert isinstance(report, str)
    # Should still work, just with current snapshot info
