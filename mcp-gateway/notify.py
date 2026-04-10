"""Send notifications to Chaguli via alerts_inbox.jsonl."""
import json
import os
import time
import logging

log = logging.getLogger("notify")

_ALERTS_DIR = os.environ.get("CHAGULI_ALERTS_DIR", "/data/alerts")


def send_alert(title: str, message: str, severity: str = "warning"):
    """Write an alert to Chaguli's alerts inbox."""
    os.makedirs(_ALERTS_DIR, exist_ok=True)
    alert_file = os.path.join(_ALERTS_DIR, "alerts_inbox.jsonl")
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "mcp-gateway",
        "severity": severity,
        "title": title,
        "message": message,
        "delivered": False,
    }
    try:
        with open(alert_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        log.info(f"Alert sent: [{severity}] {title}")
    except OSError as e:
        log.error(f"Failed to write alert: {e}")
