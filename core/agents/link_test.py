"""Test the agent link — verify the inbox watcher is picking up alerts."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.alerts.sender import AlertSender

log = logging.getLogger(__name__)

LINK_TEST_TIMEOUT = 120  # seconds to wait for delivery


def test_agent_link(data_dir: str, timeout: int = LINK_TEST_TIMEOUT) -> dict[str, Any]:
    """Send a test alert and check if the agent delivers it.

    1. Write a test alert to alerts_inbox.jsonl
    2. Poll for up to `timeout` seconds
    3. Check if the alert was marked as delivered

    Returns:
        {"status": "working"|"timeout"|"error", "wait_seconds": N, "detail": str}
    """
    sender = AlertSender(data_dir=data_dir)
    test_message = f"AgentHarness link test — {time.strftime('%Y-%m-%d %H:%M:%S')}"

    # Send test alert
    sender.send("info", test_message, source="link_test")
    log.info(f"Test alert sent. Waiting up to {timeout}s for agent to deliver...")

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        # Check if our test alert was marked delivered
        all_alerts = sender.get_all()
        for alert in all_alerts:
            if alert.get("message") == test_message and alert.get("delivered"):
                elapsed = int(time.monotonic() - start)
                return {
                    "status": "working",
                    "wait_seconds": elapsed,
                    "detail": f"Agent delivered the test alert in {elapsed} seconds",
                }
        time.sleep(5)

    elapsed = int(time.monotonic() - start)

    # Check if the alert is still pending
    pending = sender.get_pending()
    has_test = any(a.get("message") == test_message for a in pending)

    if has_test:
        return {
            "status": "timeout",
            "wait_seconds": elapsed,
            "detail": (
                f"Test alert was NOT delivered after {elapsed}s. "
                "The agent may not be reading alerts_inbox.jsonl.\n"
                "Fix: Run `agentharness generate-agent-plugin` and install the inbox watcher."
            ),
        }
    else:
        return {
            "status": "error",
            "wait_seconds": elapsed,
            "detail": "Test alert disappeared from the inbox unexpectedly.",
        }


def check_delivery_health(data_dir: str) -> dict[str, Any]:
    """Check if alerts are being delivered in a timely manner.

    Non-blocking — just checks current state without sending a test.
    """
    sender = AlertSender(data_dir=data_dir)
    pending = sender.get_pending()

    if not pending:
        return {"status": "ok", "pending": 0, "detail": "No pending alerts"}

    # Check how old the oldest pending alert is
    oldest_ts = None
    for alert in pending:
        ts = alert.get("timestamp", "")
        try:
            from datetime import datetime
            alert_time = datetime.fromisoformat(ts)
            if oldest_ts is None or alert_time < oldest_ts:
                oldest_ts = alert_time
        except (ValueError, TypeError):
            continue

    if oldest_ts:
        from datetime import datetime, timezone
        age_seconds = (datetime.now(timezone.utc) - oldest_ts.replace(tzinfo=timezone.utc)).total_seconds()
        age_hours = age_seconds / 3600

        if age_hours > 1:
            return {
                "status": "stale",
                "pending": len(pending),
                "oldest_hours": round(age_hours, 1),
                "detail": (
                    f"{len(pending)} alerts pending for {age_hours:.1f} hours. "
                    "Agent may not be reading the inbox."
                ),
            }

    return {
        "status": "ok",
        "pending": len(pending),
        "detail": f"{len(pending)} alerts pending (recently created)",
    }
