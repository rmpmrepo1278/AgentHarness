"""Structured JSON-line logger for the MCP gateway."""
import json
import os
import time
import threading
import logging
from datetime import datetime, timezone

log = logging.getLogger("gateway_log")

_LOG_FILE = os.environ.get("GATEWAY_LOG_FILE", "/data/gateway.log")
_MAX_SIZE_MB = int(os.environ.get("GATEWAY_LOG_MAX_MB", "50"))
_RETENTION_DAYS = int(os.environ.get("GATEWAY_LOG_RETENTION_DAYS", "7"))
_lock = threading.Lock()


def _rotate_if_needed():
    """Rotate log file if it exceeds max size."""
    try:
        if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > _MAX_SIZE_MB * 1024 * 1024:
            rotated = f"{_LOG_FILE}.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(_LOG_FILE, rotated)
            log_dir = os.path.dirname(_LOG_FILE) or "."
            cutoff = time.time() - (_RETENTION_DAYS * 86400)
            for f in os.listdir(log_dir):
                fp = os.path.join(log_dir, f)
                if f.startswith(os.path.basename(_LOG_FILE) + ".") and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
    except OSError:
        pass


def emit(event: str, **kwargs):
    """Write a structured log event."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    with _lock:
        _rotate_if_needed()
        os.makedirs(os.path.dirname(_LOG_FILE) or ".", exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")


def recent(limit: int = 50, event_filter: str = None) -> list:
    """Read recent log entries, optionally filtered by event type."""
    if not os.path.exists(_LOG_FILE):
        return []
    entries = []
    try:
        with open(_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if event_filter and entry.get("event") != event_filter:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries[-limit:]
