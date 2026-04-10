# tests/test_notify.py
from __future__ import annotations

import json
import stat
import pytest
from pathlib import Path

from core.doctor.notify import NotificationRouter


@pytest.fixture
def env(tmp_path):
    """Set up data_dir, inbox_dir, and a dummy alert script."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()

    # Create a tiny alert script that writes a marker file when called
    alert_script = tmp_path / "alert.sh"
    marker = tmp_path / "alert_called"
    alert_script.write_text(
        f"#!/usr/bin/env bash\necho \"$1|$2|$3\" > {marker}\n"
    )
    alert_script.chmod(alert_script.stat().st_mode | stat.S_IEXEC)

    return {
        "data_dir": str(data_dir),
        "inbox_dir": str(inbox_dir),
        "alert_script": str(alert_script),
        "marker": marker,
        "tmp_path": tmp_path,
    }


def _read_log(env) -> list[dict]:
    log_file = Path(env["data_dir"]) / "doctor_log.jsonl"
    if not log_file.exists():
        return []
    return [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]


def _inbox_files(env) -> list[Path]:
    return sorted(Path(env["inbox_dir"]).glob("doctor_*.json"))


# -- log_silent --------------------------------------------------------------


class TestLogSilent:
    def test_writes_to_log(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.log_silent("disk ok", "all clear")

        entries = _read_log(env)
        assert len(entries) == 1
        assert entries[0]["level"] == "silent"
        assert entries[0]["title"] == "disk ok"
        assert entries[0]["body"] == "all clear"
        assert entries[0]["runbook"] is None

    def test_does_not_write_inbox(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.log_silent("disk ok", "all clear")
        assert _inbox_files(env) == []

    def test_runbook_included(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.log_silent("check", "details", runbook="restart docker")
        entries = _read_log(env)
        assert entries[0]["runbook"] == "restart docker"


# -- send_fyi ----------------------------------------------------------------


class TestSendFyi:
    def test_writes_to_log_and_inbox(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.send_fyi("memory high", "usage at 85%")

        entries = _read_log(env)
        assert len(entries) == 1
        assert entries[0]["level"] == "fyi"

        inbox = _inbox_files(env)
        assert len(inbox) == 1
        payload = json.loads(inbox[0].read_text())
        assert payload["title"] == "memory high"
        assert payload["body"] == "usage at 85%"
        assert payload["_source"] == "agentharness_doctor"

    def test_does_not_call_alert(self, env):
        router = NotificationRouter(
            env["data_dir"], env["inbox_dir"], env["alert_script"]
        )
        router.send_fyi("info", "nothing urgent")
        assert not env["marker"].exists()


# -- send_critical -----------------------------------------------------------


class TestSendCritical:
    def test_writes_log_inbox_and_calls_alert(self, env):
        router = NotificationRouter(
            env["data_dir"], env["inbox_dir"], env["alert_script"]
        )
        router.send_critical("disk full", "/data at 99%", runbook="expand volume")

        # Log
        entries = _read_log(env)
        assert len(entries) == 1
        assert entries[0]["level"] == "critical"

        # Inbox
        inbox = _inbox_files(env)
        assert len(inbox) == 1
        payload = json.loads(inbox[0].read_text())
        assert payload["runbook"] == "expand volume"

        # Alert script called
        assert env["marker"].exists()
        marker_text = env["marker"].read_text()
        assert "CRITICAL" in marker_text
        assert "disk full" in marker_text

    def test_no_alert_script_still_logs(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.send_critical("disk full", "/data at 99%")

        entries = _read_log(env)
        assert len(entries) == 1
        assert entries[0]["level"] == "critical"
        inbox = _inbox_files(env)
        assert len(inbox) == 1


# -- notify routing ----------------------------------------------------------


class TestNotifyRouting:
    def test_routes_silent(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.notify("silent", "ok", "fine")
        assert len(_read_log(env)) == 1
        assert _read_log(env)[0]["level"] == "silent"
        assert _inbox_files(env) == []

    def test_routes_fyi(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.notify("fyi", "heads up", "thing happened")
        assert _read_log(env)[0]["level"] == "fyi"
        assert len(_inbox_files(env)) == 1

    def test_routes_critical(self, env):
        router = NotificationRouter(
            env["data_dir"], env["inbox_dir"], env["alert_script"]
        )
        router.notify("critical", "down", "service offline")
        assert _read_log(env)[0]["level"] == "critical"
        assert len(_inbox_files(env)) == 1
        assert env["marker"].exists()

    def test_unknown_level_falls_back_to_fyi(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.notify("banana", "weird", "unknown level")
        assert _read_log(env)[0]["level"] == "fyi"
        assert len(_inbox_files(env)) == 1

    def test_case_insensitive(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        router.notify("SILENT", "upper", "case test")
        assert _read_log(env)[0]["level"] == "silent"
