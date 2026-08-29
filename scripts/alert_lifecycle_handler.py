#!/usr/bin/env python3
"""alert_lifecycle_handler.py — Processes Telegram /ack, /snooze, /alerts commands.

Designed to be called by the inbox_watcher or as a standalone script.
Handles alert lifecycle management: acknowledge, snooze, and status.

Usage:
  python3 alert_lifecycle_handler.py --action ack --alert-id <id>
  python3 alert_lifecycle_handler.py --action snooze --alert-id <id> --hours 4
  python3 alert_lifecycle_handler.py --action status
"""

import json
import re
import sys
from pathlib import Path

# Ensure homelab_ops is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
import homelab_ops as ops

ALERT_LIFECYCLE_PATH = ops.ALERT_LIFECYCLE_PATH


def handle_telegram_command(text: str) -> str:
    """Parse a Telegram message and handle ack/snooze/status commands.

    Returns a response string to send back to Telegram.

    Supported commands:
      /ack <alert_id>          — Acknowledge an alert
      /snooze <alert_id> <h>   — Snooze an alert for N hours (default 4)
      /alerts                  — Show all active alert lifecycle entries
    """
    text = text.strip()
    lower = text.lower()

    # /ack <alert_id>
    ack_match = re.match(r'^/ack\s+(\S+)', lower)
    if ack_match:
        alert_id = ack_match.group(1)
        result = ops.ack_alert(alert_id)
        if result.get("ok"):
            return f"Acknowledged alert: {alert_id}"
        else:
            return f"Failed to acknowledge: {result.get('error', 'unknown')}"

    # /snooze <alert_id> [hours]
    snooze_match = re.match(r'^/snooze\s+(\S+)(?:\s+(\d+))?', lower)
    if snooze_match:
        alert_id = snooze_match.group(1)
        hours = int(snooze_match.group(2)) if snooze_match.group(2) else 4
        result = ops.snooze_alert(alert_id, hours)
        if result.get("ok"):
            return f"Snoozed alert {alert_id} for {hours}h"
        else:
            return f"Failed to snooze: {result.get('error', 'unknown')}"

    # /alerts
    if lower.strip() == "/alerts":
        result = ops.alert_status()
        return result.get("summary", "No alerts")

    # /report
    if lower.strip() == "/report":
        result = ops.get_daily_report()
        return result.get("report", "Report generation failed")

    return None


def main():
    """CLI entry point for testing or direct invocation."""
    import argparse
    parser = argparse.ArgumentParser(description="Alert Lifecycle Handler")
    parser.add_argument("--action", choices=["ack", "snooze", "status"])
    parser.add_argument("--alert-id", help="Alert ID to act on")
    parser.add_argument("--hours", type=int, default=4, help="Snooze duration in hours")
    parser.add_argument("--message", help="Raw Telegram message text to parse")
    args = parser.parse_args()

    if args.message:
        response = handle_telegram_command(args.message)
        if response:
            print(response)
        else:
            print("Unrecognized command. Use /ack <id>, /snooze <id> [h], /alerts, or /report")
        return

    if args.action == "ack" and args.alert_id:
        result = ops.ack_alert(args.alert_id)
        print(json.dumps(result, indent=2))
    elif args.action == "snooze" and args.alert_id:
        result = ops.snooze_alert(args.alert_id, args.hours)
        print(json.dumps(result, indent=2))
    elif args.action == "status":
        result = ops.alert_status()
        print(result.get("summary", json.dumps(result, indent=2)))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
