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


# -- daily digest ------------------------------------------------------------


def _write_log_entries(env, entries: list[dict]) -> None:
    """Write pre-built log entries to doctor_log.jsonl."""
    log_file = Path(env["data_dir"]) / "doctor_log.jsonl"
    with log_file.open("w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _make_entry(title: str, body: str, level: str = "silent", hours_ago: float = 0) -> dict:
    """Build a log entry dict with a timestamp *hours_ago* hours in the past."""
    from datetime import timedelta
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "timestamp": ts.isoformat(),
        "level": level,
        "title": title,
        "body": body,
        "runbook": None,
    }


class TestGenerateDigest:
    def test_returns_none_when_no_log(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        assert router.generate_digest() is None

    def test_returns_none_when_all_old(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("old-issue", "fixed in 5s", hours_ago=30),
        ])
        assert router.generate_digest() is None

    def test_includes_recent_fixed_entries(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("llm-server-offline", "fixed in 16s", hours_ago=2),
            _make_entry("container-crashed", "fixed in 12s", hours_ago=1),
            _make_entry("disk-pressure", "fixed in 8s", hours_ago=0.5),
        ])
        digest = router.generate_digest()
        assert digest is not None
        assert "Auto-healed (3)" in digest
        assert "llm-server-offline" in digest
        assert "container-crashed" in digest
        assert "disk-pressure" in digest
        assert "All quiet: 0 failures today" in digest

    def test_groups_escalated_and_failed(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("llm-server-offline", "fixed in 16s", hours_ago=2),
            _make_entry("chaguli-down", "escalated — container keeps crashing", hours_ago=1),
            _make_entry("dns-broken", "failed to restart", hours_ago=0.5),
        ])
        digest = router.generate_digest()
        assert "Auto-healed (1)" in digest
        assert "Needs attention (2)" in digest
        assert "chaguli-down" in digest
        assert "dns-broken" in digest
        # Should NOT say "All quiet" when there are failures
        assert "All quiet" not in digest

    def test_includes_cooldown_entries(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("flaky-service", "cooldown — retried too often", hours_ago=1),
        ])
        digest = router.generate_digest()
        assert "In cooldown (1)" in digest
        assert "flaky-service" in digest

    def test_excludes_old_entries(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("old-fix", "fixed in 5s", hours_ago=30),
            _make_entry("recent-fix", "fixed in 3s", hours_ago=1),
        ])
        digest = router.generate_digest()
        assert "old-fix" not in digest
        assert "recent-fix" in digest


class TestSendDigest:
    def test_returns_false_when_nothing(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        assert router.send_digest() is False
        assert _inbox_files(env) == []

    def test_returns_true_and_creates_inbox_file(self, env):
        router = NotificationRouter(env["data_dir"], env["inbox_dir"])
        _write_log_entries(env, [
            _make_entry("llm-server-offline", "fixed in 16s", hours_ago=2),
            _make_entry("chaguli-down", "escalated — keeps crashing", hours_ago=1),
        ])
        result = router.send_digest()
        assert result is True

        inbox = _inbox_files(env)
        assert len(inbox) == 1
        payload = json.loads(inbox[0].read_text())
        assert payload["title"] == "Homelab Doctor — Daily Digest"
        assert "Auto-healed" in payload["body"]
        assert "Needs attention" in payload["body"]
        assert payload["_source"] == "agentharness_doctor"
