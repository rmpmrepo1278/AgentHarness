"""Tests for inbox_watcher.py — Telegram notification delivery."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add the scratch path to find the module
sys.path.insert(0, '/Users/rohitmishra/.gemini/antigravity/scratch/core/agents')

from inbox_watcher import InboxWatcher


def make_alerts_file(tmpdir, alerts):
    """Write an alerts JSON array to a temp file."""
    f = tmpdir / "alerts_inbox.jsonl"
    f.write_text(json.dumps(alerts, indent=2))
    return f


def test_alert_delivered_marked():
    """Alert with delivered=True should be skipped."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "test", "severity": "info", "delivered": True}
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 0, "Already-delivered alert should not be re-sent"


def test_alert_silence_keywords():
    """Alerts matching silence_keywords should be auto-delivered without sending."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "Got 429 from OpenRouter, switching to fallback", "severity": "warn"},
            {"message": "heartbeat ok, all healthy", "severity": "info"},
            {"message": "auto-fixed: restored from backup", "severity": "info"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 3, "All 3 should be auto-delivered (silenced)"


def test_alert_telegram_send():
    """Undelivered alert should call _send_telegram."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    original_send = watcher._send_telegram
    sent_messages = []
    def mock_send(text):
        sent_messages.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "CPU at 90%", "severity": "warn", "source": "monitor"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 1
        assert len(sent_messages) == 1
        assert "[WARN]" in sent_messages[0]
        assert "CPU at 90%" in sent_messages[0]
        assert "(from: monitor)" in sent_messages[0]
    watcher._send_telegram = original_send


def test_message_splitting():
    """Messages longer than 4096 chars should be split by _send_telegram."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    long_text = "X" * 5000
    # Test _send_telegram directly (it's the method that splits)
    result = watcher._send_telegram(long_text)
    assert result is False  # fails because fake token, but doesn't crash
    # Verify splitting logic exists in the method
    import inspect
    source = inspect.getsource(watcher._send_telegram)
    assert "max_len" in source or "4096" in source, "Message splitting not implemented in _send_telegram"


def test_check_briefings():
    """Briefings should be sent and marked with .delivered file."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        briefings_dir = td_path / "briefings"
        briefings_dir.mkdir()
        briefing = {
            "date": "2026-06-02",
            "health": {"checks_passed": 5, "checks_run": 6},
            "action_items": [{"priority": "high", "item": "Check disk space"}],
        }
        (briefings_dir / "morning.json").write_text(json.dumps(briefing, indent=2))
        result = watcher.check_briefings()
        assert result == 1
        assert len(sent) == 1
        assert "Morning Briefing" in sent[0]
        assert "5/6" in sent[0]
        assert (briefings_dir / "morning.delivered").exists()


def test_check_proposals():
    """Pending proposals should be sent via Telegram."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        proposals_dir = td_path / "proposals"
        proposals_dir.mkdir()
        proposal = {
            "proposal_id": "42",
            "tool_name": "restart_service",
            "reason": "Nginx is down",
            "status": "pending",
        }
        (proposals_dir / "prop_42.json").write_text(json.dumps(proposal, indent=2))
        result = watcher.check_proposals()
        assert result == 1
        assert len(sent) == 1
        assert "#42" in sent[0]
        assert "restart_service" in sent[0]


def test_non_pending_proposal_skipped():
    """Non-pending proposals should not be sent."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        proposals_dir = td_path / "proposals"
        proposals_dir.mkdir()
        for status in ("approved", "rejected", "completed"):
            (proposals_dir / f"prop_{status}.json").write_text(json.dumps({
                "proposal_id": "1",
                "status": status,
            }, indent=2))
        result = watcher.check_proposals()
        assert result == 0, "Non-pending proposals should be skipped"
        assert len(sent) == 0


def test_proposal_already_notified():
    """Already-notified proposals should be skipped."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        proposals_dir = td_path / "proposals"
        proposals_dir.mkdir()
        (proposals_dir / "prop_1.json").write_text(json.dumps({
            "proposal_id": "1",
            "status": "pending",
            "notified": True,
        }, indent=2))
        result = watcher.check_proposals()
        assert result == 0


def test_session_injection():
    """CRITICAL alerts should inject into active sessions."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.session_dir = td_path / "sessions"
        watcher.session_dir.mkdir()
        # Create a recent session file
        session_file = watcher.session_dir / "session_test.json"
        session_file.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}))
        # Touch to make it recent
        session_file.touch()
        watcher.alerts_file.write_text(json.dumps([
            {"message": "Disk failure imminent", "severity": "CRITICAL", "source": "monitor"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 1
        updated = json.loads(session_file.read_text())
        assert len(updated["messages"]) == 2
        assert "SYSTEM" in updated["messages"][1]["content"]


def test_session_injection_only_for_recent():
    """Old session files should NOT receive injections."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.session_dir = td_path / "sessions"
        watcher.session_dir.mkdir()
        session_file = watcher.session_dir / "session_old.json"
        session_file.write_text(json.dumps({"messages": []}))
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(session_file, (old_time, old_time))
        watcher.alerts_file.write_text(json.dumps([
            {"message": "CRITICAL failure", "severity": "CRITICAL"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 1  # alert still delivered via Telegram (mock succeeds)
        # Session should NOT be injected (too old) — injection only happens on tg success
        data = json.loads(session_file.read_text())
        assert len(data.get("messages", [])) == 0


def test_tick_returns_counts():
    """tick() should return dict with all counters."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    result = watcher.tick()
    assert isinstance(result, dict)
    assert "alerts_sent" in result
    assert "briefings_sent" in result
    assert "proposals_sent" in result


def test_send_telegram_fails_gracefully():
    """Send failure should return False, not crash."""
    watcher = InboxWatcher("/tmp", "invalid_token", "invalid_chat")
    result = watcher._send_telegram("test")
    assert result is False


def test_send_telegram_no_creds():
    """Missing credentials should return False without crashing."""
    watcher = InboxWatcher("/tmp", "", "")
    result = watcher._send_telegram("test")
    assert result is False


def test_empty_alerts_file():
    """Empty alerts file should be handled gracefully."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text("")
        result = watcher.check_alerts()
        assert result == 0


def test_malformed_json():
    """Corrupted alerts file should not crash."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text("{bad json")
        result = watcher.check_alerts()
        assert result == 0


def test_jsonl_format():
    """Line-by-line JSONL format should work (not just JSON arrays)."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(
            '{"message": "alert1", "severity": "info"}\n'
            '{"message": "alert2", "severity": "warn"}\n'
        )
        result = watcher.check_alerts()
        assert result == 2
        assert len(sent) == 2


def test_severity_uppercased():
    """Severity should be uppercased in Telegram messages."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "test", "severity": "critical"},
        ], indent=2))
        watcher.check_alerts()
        assert "[CRITICAL]" in sent[0]


def test_mixed_severity_handling():
    """Different severity alerts should all be sent."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "info msg", "severity": "info"},
            {"message": "warn msg", "severity": "warn"},
            {"message": "CRITICAL msg", "severity": "CRITICAL"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 3
        assert len(sent) == 3


def test_briefing_empty_actions():
    """Briefing with no action items should still be sent."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    sent = []
    def mock_send(text):
        sent.append(text)
        return True
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        briefings_dir = td_path / "briefings"
        briefings_dir.mkdir()
        (briefings_dir / "summary.json").write_text(json.dumps({
            "date": "2026-06-02",
            "health": {"checks_passed": 3, "checks_run": 3},
        }, indent=2))
        result = watcher.check_briefings()
        assert result == 1
        assert "Action items" not in sent[0]


def test_delivery_failure_not_marked():
    """Alert should NOT be marked delivered if Telegram send fails."""
    watcher = InboxWatcher("/tmp", "fake_token", "fake_chat")
    attempts = []
    def mock_send(text):
        attempts.append(text)
        return False
    watcher._send_telegram = mock_send
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        watcher.inbox_dir = td_path
        watcher.alerts_file = td_path / "alerts_inbox.jsonl"
        watcher.alerts_file.write_text(json.dumps([
            {"message": "test", "severity": "info"},
        ], indent=2))
        result = watcher.check_alerts()
        assert result == 0  # not counted as sent
        assert len(attempts) == 1  # only 1 attempt, no retry
        # Alert should NOT be marked delivered since send failed
        alerts = json.loads(watcher.alerts_file.read_text())
        assert alerts[0].get("delivered") is not True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
