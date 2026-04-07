"""Tests for watchdog — heartbeat, stale lock recovery, process monitoring."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from core.resilience.watchdog import (
    check_heartbeat,
    recover_stale_lock,
    write_heartbeat,
)


def test_heartbeat_write(tmp_path: Path) -> None:
    """write_heartbeat creates heartbeat.json with timestamp and pid."""
    write_heartbeat(tmp_path)
    hb_file = tmp_path / "heartbeat.json"
    assert hb_file.exists()
    data = json.loads(hb_file.read_text())
    assert "timestamp" in data
    assert data["pid"] == os.getpid()
    assert "iso" in data


def test_check_heartbeat_fresh(tmp_path: Path) -> None:
    """Freshly written heartbeat is 'ok'."""
    write_heartbeat(tmp_path)
    result = check_heartbeat(tmp_path)
    assert result["status"] == "ok"
    assert result["age_seconds"] < 5
    assert result["pid"] == os.getpid()


def test_check_heartbeat_stale(tmp_path: Path) -> None:
    """Heartbeat from 30 min ago with max_age=900 is 'stale'."""
    write_heartbeat(tmp_path)
    # Backdate the heartbeat by 1800 seconds
    hb_file = tmp_path / "heartbeat.json"
    data = json.loads(hb_file.read_text())
    data["timestamp"] = time.time() - 1800
    hb_file.write_text(json.dumps(data))

    result = check_heartbeat(tmp_path, max_age_seconds=900)
    assert result["status"] == "stale"
    assert result["age_seconds"] >= 1800 - 1  # allow tiny drift


def test_check_heartbeat_missing(tmp_path: Path) -> None:
    """No heartbeat file is 'missing'."""
    result = check_heartbeat(tmp_path)
    assert result["status"] == "missing"


def test_stale_lock_recovery(tmp_path: Path) -> None:
    """Lock file with dead PID (99999999) gets removed, returns True."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("99999999")
    assert recover_stale_lock(lock_file) is True
    assert not lock_file.exists()


def test_stale_lock_alive_pid_not_removed(tmp_path: Path) -> None:
    """Lock file with our own PID stays, returns False."""
    lock_file = tmp_path / "test.lock"
    lock_file.write_text(str(os.getpid()))
    assert recover_stale_lock(lock_file) is False
    assert lock_file.exists()
