#!/usr/bin/env python3
"""
disk_trend.py — Predictive disk usage alerting.

Appends current disk usage to data/disk_trend.jsonl, then calculates a
linear growth rate from the first and last data points.  If the disk is
predicted to hit 90% within 14 days, an alert is written to the Chaguli
inbox via the standard AlertSender.

Called by the scheduler via harness_registry.yaml.

Usage:
    python3 scripts/disk_trend.py
"""

import json
import shutil
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
TREND_FILE = DATA_DIR / "disk_trend.jsonl"

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
ALERT_TARGET_PERCENT = 90   # predict when disk reaches this %
ALERT_HORIZON_DAYS = 14     # alert if predicted full within this many days

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_disk_usage() -> dict:
    """Return current disk stats using shutil.disk_usage (cross-platform)."""
    usage = shutil.disk_usage("/")
    total_gb = usage.total / (1024 ** 3)
    used_gb = usage.used / (1024 ** 3)
    percent = (usage.used / usage.total) * 100
    return {
        "timestamp": time.time(),
        "used_gb": round(used_gb, 2),
        "total_gb": round(total_gb, 2),
        "percent": round(percent, 1),
    }


def load_history() -> list[dict]:
    """Load all data points from the JSONL file."""
    if not TREND_FILE.exists():
        return []
    entries = []
    for line in TREND_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def append_entry(entry: dict) -> None:
    """Append one JSON entry to the trend file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with TREND_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def send_alert(message: str) -> None:
    """Write a warning-level alert to the Chaguli inbox."""
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from core.alerts.sender import get_alert_sender
        sender = get_alert_sender()
        sender.send("warning", message, source="disk_trend")
    except Exception:
        # Fallback: write directly to alerts_inbox.jsonl
        alert_file = DATA_DIR / "alerts_inbox.jsonl"
        alert = {
            "severity": "warning",
            "message": message,
            "source": "disk_trend",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "delivered": False,
        }
        with alert_file.open("a") as f:
            f.write(json.dumps(alert) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # 1. Record current usage
    current = current_disk_usage()
    append_entry(current)

    free_gb = current["total_gb"] - current["used_gb"]
    summary = f"Disk: {current['percent']}% ({free_gb:.0f}GB free)"

    # 2. Load history and check if we can project
    history = load_history()
    if len(history) < 2:
        print(f"{summary} — not enough data points yet for projection")
        return 0

    first = history[0]
    last = history[-1]
    elapsed_seconds = last["timestamp"] - first["timestamp"]
    elapsed_days = elapsed_seconds / 86400

    if elapsed_days < 1.0:
        print(f"{summary} — need >= 24h of data for projection (have {elapsed_days:.1f}d)")
        return 0

    # 3. Linear extrapolation: growth rate in GB/day
    growth_gb_per_day = (last["used_gb"] - first["used_gb"]) / elapsed_days

    if growth_gb_per_day <= 0:
        print(f"{summary}, shrinking {abs(growth_gb_per_day):.2f}GB/day — no risk")
        return 0

    # 4. Predict days until ALERT_TARGET_PERCENT
    target_gb = current["total_gb"] * (ALERT_TARGET_PERCENT / 100)
    remaining_gb = target_gb - current["used_gb"]

    if remaining_gb <= 0:
        days_until_full = 0
    else:
        days_until_full = remaining_gb / growth_gb_per_day

    summary += f", growing {growth_gb_per_day:.2f}GB/day, {ALERT_TARGET_PERCENT}% in {days_until_full:.0f} days"
    print(summary)

    # 5. Alert if within horizon
    if days_until_full < ALERT_HORIZON_DAYS:
        alert_msg = (
            f"Disk predicted to reach {ALERT_TARGET_PERCENT}% in {days_until_full:.0f} days "
            f"(growing {growth_gb_per_day:.2f}GB/day, currently {current['percent']}%). "
            f"Consider cleanup or expanding storage."
        )
        send_alert(alert_msg)
        print(f"ALERT sent: {alert_msg}")
        return 1  # non-zero exit signals the registry_engine that something needs attention

    return 0


if __name__ == "__main__":
    sys.exit(main())
