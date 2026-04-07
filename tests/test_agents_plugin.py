from __future__ import annotations
import json
import os
import pytest
from pathlib import Path


def test_inbox_watcher_check_alerts(tmp_path):
    from core.agents.inbox_watcher import InboxWatcher
    # Create an alert
    alerts = [{"severity": "warn", "message": "Test alert", "delivered": False, "source": "test"}]
    (tmp_path / "alerts_inbox.jsonl").write_text(json.dumps(alerts))

    # Watcher without real Telegram (will fail to send, alert stays undelivered)
    watcher = InboxWatcher(inbox_dir=str(tmp_path), telegram_token="", telegram_chat_id="")
    sent = watcher.check_alerts()
    assert sent == 0  # No creds, can't send


def test_inbox_watcher_check_briefings(tmp_path):
    from core.agents.inbox_watcher import InboxWatcher
    briefings_dir = tmp_path / "briefings"
    briefings_dir.mkdir()
    (briefings_dir / "2026-04-07.json").write_text(json.dumps({"date": "2026-04-07", "health": {"checks_run": 10, "checks_passed": 9}}))

    watcher = InboxWatcher(inbox_dir=str(tmp_path), telegram_token="", telegram_chat_id="")
    sent = watcher.check_briefings()
    assert sent == 0  # No creds


def test_inbox_watcher_check_proposals(tmp_path):
    from core.agents.inbox_watcher import InboxWatcher
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()
    proposal = {"proposal_id": "42", "tool_name": "disk-cleanup", "reason": "Low space", "status": "pending"}
    (proposals_dir / "42.json").write_text(json.dumps(proposal))

    watcher = InboxWatcher(inbox_dir=str(tmp_path), telegram_token="", telegram_chat_id="")
    sent = watcher.check_proposals()
    assert sent == 0  # No creds


def test_inbox_watcher_tick(tmp_path):
    from core.agents.inbox_watcher import InboxWatcher
    watcher = InboxWatcher(inbox_dir=str(tmp_path), telegram_token="", telegram_chat_id="")
    result = watcher.tick()
    assert result == {"alerts_sent": 0, "briefings_sent": 0, "proposals_sent": 0}


def test_inbox_watcher_format_briefing():
    from core.agents.inbox_watcher import InboxWatcher
    watcher = InboxWatcher(inbox_dir="/tmp", telegram_token="", telegram_chat_id="")
    briefing = {
        "date": "2026-04-07",
        "health": {"checks_run": 10, "checks_passed": 9},
        "action_items": [
            {"priority": "high", "item": "Update firmware"},
            {"priority": "low", "item": "Clean logs"},
        ],
    }
    text = watcher._format_briefing(briefing)
    assert "2026-04-07" in text
    assert "9/10" in text
    assert "Update firmware" in text


def test_generate_plugin(tmp_path):
    from core.agents.plugin_generator import generate_plugin
    output = generate_plugin(
        output_dir=str(tmp_path / "plugin"),
        inbox_dir="/opt/agentharness/data",
        agent_type="chaguli",
    )
    assert Path(output).exists()
    assert (Path(output) / "inbox_watcher.py").exists()
    assert (Path(output) / "INSTALL.md").exists()
    assert (Path(output) / "agentharness-inbox-watcher.service").exists()
    assert (Path(output) / ".env").exists()


def test_generate_plugin_instructions_have_paths(tmp_path):
    from core.agents.plugin_generator import generate_plugin
    output = generate_plugin(
        output_dir=str(tmp_path / "plugin"),
        inbox_dir="/home/user/agentharness/data",
    )
    install_md = (Path(output) / "INSTALL.md").read_text()
    assert "/home/user/agentharness/data" in install_md


def test_generate_plugin_env_not_overwritten(tmp_path):
    from core.agents.plugin_generator import generate_plugin
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    env_file = plugin_dir / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=secret\n")

    generate_plugin(output_dir=str(plugin_dir), inbox_dir="/data")
    assert "secret" in env_file.read_text()


def test_generate_plugin_watcher_is_executable(tmp_path):
    from core.agents.plugin_generator import generate_plugin
    output = generate_plugin(output_dir=str(tmp_path / "plugin"), inbox_dir="/data")
    watcher = Path(output) / "inbox_watcher.py"
    assert os.access(str(watcher), os.X_OK)


def test_check_delivery_health_no_alerts(tmp_path):
    from core.agents.link_test import check_delivery_health
    result = check_delivery_health(data_dir=str(tmp_path))
    assert result["status"] == "ok"
    assert result["pending"] == 0


def test_check_delivery_health_stale_alerts(tmp_path):
    from core.agents.link_test import check_delivery_health
    # Create an old undelivered alert
    alerts = [{"severity": "warn", "message": "Old", "delivered": False, "timestamp": "2020-01-01T00:00:00+00:00"}]
    (tmp_path / "alerts_inbox.jsonl").write_text(json.dumps(alerts))
    result = check_delivery_health(data_dir=str(tmp_path))
    assert result["status"] == "stale"
    assert result["pending"] == 1
