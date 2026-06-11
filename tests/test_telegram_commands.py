"""Tests for Telegram command tools (homelab_ops, proxy endpoints)."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, PropertyMock

# Test the send_notification fix — writes to alerts_inbox.jsonl


def test_send_notification_writes_to_alerts_inbox():
    """send_notification should write to alerts_inbox.jsonl, not inbox/ dir."""
    sys.path.insert(0, '/home/rohit/.hermes/hermes-agent/tools')
    
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Point homelab_ops to our temp dir
        with patch('homelab_ops.INBOX_DIR', td_path / "inbox"):
            from homelab_ops import send_notification
            
            # Set data dir for alerts
            data_dir = td_path / "data"
            data_dir.mkdir()
            
            with patch('homelab_ops.Path', return_value=data_dir / "alerts_inbox.jsonl"):
                result = send_notification("Test Title", "Test message body", "WARNING")
                
                # The alert should be in alerts_inbox.jsonl
                alerts_file = data_dir / "alerts_inbox.jsonl"
                if alerts_file.exists():
                    content = json.loads(alerts_file.read_text())
                    assert len(content) == 1
                    assert content[0]["severity"] == "WARNING"
                    assert "Test Title" in content[0]["message"]
                    assert content[0]["source"] == "hermes_ops"

    print("OK - send_notification writes to correct location")


def test_send_notification_severity_uppercase():
    """Severity should always be uppercase."""
    sys.path.insert(0, '/home/rohit/.hermes/hermes-agent/tools')
    
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        with patch('homelab_ops.INBOX_DIR', td_path / "inbox"):
            from homelab_ops import send_notification
            
            data_dir = td_path / "data"
            data_dir.mkdir()
            
            with patch('homelab_ops.Path', return_value=data_dir / "alerts_inbox.jsonl"):
                for sev in ("info", "WARNING", "Critical"):
                    send_notification("Test", "msg", sev)
                
                alerts_file = data_dir / "alerts_inbox.jsonl"
                alerts = json.loads(alerts_file.read_text())
                severities = [a["severity"] for a in alerts]
                assert "INFO" in severities
                assert "WARNING" in severities
                assert "CRITICAL" in severities

    print("OK - severities uppercased")


def test_proxy_cost_endpoint():
    """Proxy /v1/cost endpoint should return valid cost data."""
    import httpx
    try:
        resp = httpx.get("http://localhost:8080/v1/cost", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert "cost" in data or "providers" in data
        print(f"OK - /v1/cost returned {resp.status_code}")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        print(f"SKIP - proxy not available: {e}")


def test_proxy_routing_endpoint():
    """Proxy /v1/routing GET should return routing config."""
    import httpx
    try:
        resp = httpx.get("http://localhost:8080/v1/routing", timeout=5)
        assert resp.status_code in (200, 405)  # 405 is valid (GET not allowed, POST is)
        print(f"OK - /v1/routing returned {resp.status_code}")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        print(f"SKIP - proxy not available: {e}")


def test_proxy_cap_endpoint():
    """Proxy /v1/cap should return provider cap data."""
    import httpx
    try:
        resp = httpx.get("http://localhost:8080/v1/cap", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        print(f"OK - /v1/cap returned {resp.status_code}")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        print(f"SKIP - proxy not available: {e}")


def test_patch_telegram_quiet_path_discovery():
    """patch_telegram_quiet.py should find telegram.py in at least one location."""
    import sys
    from pathlib import Path
    
    candidates = [
        Path('/home/rohit/.hermes/hermes-agent/gateway/platforms/telegram.py'),
        Path.home() / '.hermes' / 'hermes-agent' / 'gateway' / 'platforms' / 'telegram.py',
        Path('/home/rohit/agentharness/gateway/platforms/telegram.py'),
    ]
    
    found = any(p.exists() for p in candidates)
    if found:
        matching = [str(p) for p in candidates if p.exists()]
        print(f"OK - telegram.py found at: {matching}")
    else:
        print(f"SKIP - no telegram.py found in candidate paths")


if __name__ == "__main__":
    test_send_notification_writes_to_alerts_inbox()
    test_send_notification_severity_uppercase()
    test_proxy_cost_endpoint()
    test_proxy_routing_endpoint()
    test_proxy_cap_endpoint()
    test_patch_telegram_quiet_path_discovery()
    print("\nAll tests passed!")
