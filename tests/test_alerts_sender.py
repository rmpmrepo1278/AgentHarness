"""Tests for the file-based alert sender."""
from __future__ import annotations

import json
import pytest
from pathlib import Path


@pytest.fixture
def alert_dir(tmp_path):
    return tmp_path


def test_send_creates_alert(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.send("warn", "Disk at 87%", source="disk_check")
    alerts = sender.get_all()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warn"
    assert alerts[0]["message"] == "Disk at 87%"
    assert alerts[0]["source"] == "disk_check"


def test_send_convenience_methods(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.info("All good")
    sender.warn("Getting full")
    sender.critical("Disk full!")
    alerts = sender.get_all()
    assert len(alerts) == 3
    assert alerts[0]["severity"] == "info"
    assert alerts[1]["severity"] == "warn"
    assert alerts[2]["severity"] == "critical"


def test_get_pending_excludes_delivered(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.send("info", "Alert 1")
    sender.send("warn", "Alert 2")
    sender.mark_delivered(count=1)
    pending = sender.get_pending()
    assert len(pending) == 1
    assert pending[0]["message"] == "Alert 2"


def test_mark_all_delivered(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.send("info", "A")
    sender.send("warn", "B")
    sender.send("critical", "C")
    marked = sender.mark_delivered()
    assert marked == 3
    assert len(sender.get_pending()) == 0


def test_clear_old_removes_delivered(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.send("info", "Old alert")
    sender.mark_delivered()
    # Force the timestamp to be old
    alerts = json.loads(sender.alerts_file.read_text())
    alerts[0]["timestamp"] = "2020-01-01T00:00:00+00:00"
    sender.alerts_file.write_text(json.dumps(alerts))
    removed = sender.clear_old(max_age_days=1)
    assert removed == 1
    assert len(sender.get_all()) == 0


def test_no_telegram_api_calls(alert_dir):
    """Verify the alert sender makes NO direct Telegram API calls."""
    import inspect
    from core.alerts import sender as module
    source = inspect.getsource(module)
    assert "TELEGRAM_BOT_TOKEN" not in source
    assert "TELEGRAM_CHAT_ID" not in source
    assert "api.telegram.org" not in source
    assert "curl" not in source
    assert "import requests" not in source


def test_alert_has_timestamp(alert_dir):
    from core.alerts.sender import AlertSender
    sender = AlertSender(data_dir=str(alert_dir))
    sender.send("info", "Test")
    alerts = sender.get_all()
    assert "timestamp" in alerts[0]
    assert len(alerts[0]["timestamp"]) > 10


def test_get_alert_sender_from_env(alert_dir, monkeypatch):
    from core.alerts.sender import get_alert_sender
    monkeypatch.setenv("AH_DATA_DIR", str(alert_dir))
    sender = get_alert_sender()
    sender.send("info", "Via env")
    assert len(sender.get_all()) == 1
