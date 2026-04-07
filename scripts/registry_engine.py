#!/usr/bin/env python3
"""
registry_engine.py — Reads harness_registry.yaml and executes checks/harnesses
                     based on current window, schedule, and conditions.

Called by scheduler.sh. This is the brain that makes everything pluggable.

Usage:
    python3 registry_engine.py run_checks [--window offline|online|any]
    python3 registry_engine.py run_harnesses [--window offline|online|any]
    python3 registry_engine.py add_check NAME --command CMD --type threshold --warn N --critical N
    python3 registry_engine.py add_harness NAME --script PATH --window offline --frequency daily
    python3 registry_engine.py list
    python3 registry_engine.py status
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_AH_DATA_DIR = os.environ.get("AH_DATA_DIR", "/opt/agentharness")
_AH_CONFIG_DIR = os.environ.get("AH_CONFIG_DIR", os.path.join(_AH_DATA_DIR, "config"))
_AH_SCRIPTS_DIR = os.environ.get("AH_SCRIPTS_DIR", os.path.join(_AH_DATA_DIR, "scripts"))
_AH_CUSTOM_DIR = os.environ.get("AH_CUSTOM_DIR", os.path.join(_AH_DATA_DIR, "custom"))
_AH_LOGS_DIR = os.environ.get("AH_LOGS_DIR", os.path.join(_AH_DATA_DIR, "logs"))

REGISTRY_PATH = os.path.join(_AH_CONFIG_DIR, "harness_registry.yaml")
FALLBACK_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "config", "harness_registry.yaml")
STATE_FILE = os.path.join(_AH_DATA_DIR, "registry_state.json")
SCRIPTS_DIR = _AH_SCRIPTS_DIR
CUSTOM_DIR = _AH_CUSTOM_DIR
LOG_DIR = _AH_LOGS_DIR


def load_registry():
    """Load the YAML registry, falling back to bundled copy."""
    try:
        import yaml
    except ImportError:
        # Fallback: parse YAML-like structure manually for basic cases
        return load_registry_fallback()

    for path in [REGISTRY_PATH, FALLBACK_REGISTRY]:
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f)
    return {"checks": {}, "harnesses": {}}


def load_registry_fallback():
    """Minimal YAML parser for when PyYAML isn't installed."""
    for path in [REGISTRY_PATH, FALLBACK_REGISTRY]:
        if os.path.exists(path):
            # Parse just enough to extract check/harness names and commands
            import json
            content = open(path).read()
            # Try to convert to JSON-ish structure
            print("WARNING: PyYAML not installed. Install with: pip install pyyaml")
            return {"checks": {}, "harnesses": {}}
    return {"checks": {}, "harnesses": {}}


def load_state():
    """Load last-run state for each check/harness."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    """Save state."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def should_run(name, config, state, current_window):
    """Determine if a harness should run based on schedule and window."""
    # Check window
    harness_window = config.get("window", "any")
    if harness_window != "any" and harness_window != current_window:
        if not (harness_window == "offline" and current_window == "offline_lan"):
            return False

    # Check if enabled
    if not config.get("enabled", True):
        return False

    # Check trigger-based (file exists)
    trigger = config.get("trigger", "")
    if trigger.startswith("file_changed:"):
        trigger_file = trigger.split(":", 1)[1]
        return os.path.exists(trigger_file)

    # Check time_window (e.g., "07:00-08:00")
    time_window = config.get("time_window", "")
    if time_window:
        try:
            start_str, end_str = time_window.split("-")
            now = datetime.now()
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start = now.replace(hour=start_h, minute=start_m, second=0)
            end = now.replace(hour=end_h, minute=end_m, second=0)
            if not (start <= now <= end):
                return False
        except (ValueError, AttributeError):
            pass

    # Check frequency
    frequency = config.get("frequency", "daily")
    last_run = state.get(name, {}).get("last_run", "")

    if not last_run:
        return True  # Never run before

    try:
        last_dt = datetime.fromisoformat(last_run)
    except (ValueError, TypeError):
        return True

    now = datetime.now()
    elapsed = now - last_dt

    if frequency == "hourly":
        return elapsed > timedelta(hours=1)
    elif frequency == "daily":
        return elapsed > timedelta(days=1)
    elif frequency == "weekly":
        return elapsed > timedelta(weeks=1)
    elif frequency == "monthly":
        return elapsed > timedelta(days=30)
    elif frequency.endswith("h"):
        hours = int(frequency[:-1])
        return elapsed > timedelta(hours=hours)
    elif frequency.endswith("d"):
        days = int(frequency[:-1])
        return elapsed > timedelta(days=days)
    elif frequency.endswith("m"):
        minutes = int(frequency[:-1])
        return elapsed > timedelta(minutes=minutes)

    return elapsed > timedelta(days=1)  # Default: daily


def run_command(command, timeout=300):
    """Run a shell command and return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timed out"
    except Exception as e:
        return -1, "", str(e)


def run_checks(window="any"):
    """Run all enabled checks for the current window."""
    registry = load_registry()
    checks = registry.get("checks", {})
    state = load_state()
    alerts = []

    for name, config in checks.items():
        if not config.get("enabled", True):
            continue

        # Check if required binary exists
        requires = config.get("requires", "")
        if requires and not shutil.which(requires):
            continue

        command = config.get("command", "")
        check_type = config.get("type", "threshold")
        message_template = config.get("message", f"Check {name}: {{value}}")

        code, stdout, stderr = run_command(command, timeout=30)

        if check_type == "threshold":
            try:
                value = float(stdout)
            except (ValueError, TypeError):
                continue

            warn = float(config.get("warn", 999999))
            critical = float(config.get("critical", 999999))
            message = message_template.replace("{value}", str(int(value)))

            if value >= critical:
                alerts.append(("CRITICAL", message))
            elif value >= warn:
                alerts.append(("WARN", message))

        elif check_type == "http_probe":
            if code != 0 or "error" in stdout.lower():
                message = message_template.replace("{value}", stderr or "unreachable")
                alerts.append(("WARN", message))

        elif check_type == "command_output":
            if stdout:
                message = message_template.replace("{value}", stdout[:200])
                alerts.append(("WARN", message))

        elif check_type == "regex_match":
            expected = config.get("expected", "")
            if expected and not re.search(expected, stdout):
                message = message_template.replace("{value}", stdout[:100])
                alerts.append(("WARN", message))

        elif check_type == "command_exit":
            if code != 0:
                message = message_template.replace("{value}", stderr[:200])
                alerts.append(("WARN", message))

    # Send alerts
    for severity, message in alerts:
        subprocess.run(
            ["bash", f"{SCRIPTS_DIR}/alert.sh", severity, message],
            capture_output=True, timeout=30
        )
        print(f"[{severity}] {message}")

    print(f"Ran {len(checks)} checks, {len(alerts)} alert(s)")
    return alerts


def run_harnesses(window="any"):
    """Run all due harnesses for the current window."""
    registry = load_registry()
    harnesses = registry.get("harnesses", {})
    state = load_state()
    ran = []

    # Sort by dependencies
    for name, config in harnesses.items():
        if not should_run(name, config, state, window):
            continue

        # Check dependencies
        depends_on = config.get("depends_on", "")
        if depends_on and depends_on not in [r[0] for r in ran]:
            # Dependency hasn't run this cycle — skip for now
            continue

        script = config.get("script", "")
        if not script:
            continue

        # Resolve script path
        if not script.startswith("/"):
            # Check custom dir first, then scripts dir
            if os.path.exists(os.path.join(CUSTOM_DIR, script)):
                script = os.path.join(CUSTOM_DIR, script)
            else:
                script = os.path.join(SCRIPTS_DIR, script)

        description = config.get("description", name)
        print(f"Running: {name} — {description}")

        code, stdout, stderr = run_command(f"bash {script}", timeout=1800)

        state[name] = {
            "last_run": datetime.now().isoformat(),
            "exit_code": code,
            "output_tail": stdout[-200:] if stdout else "",
            "error_tail": stderr[-200:] if stderr else "",
        }

        if code == 0:
            print(f"  OK: {name}")
            ran.append((name, True))
        else:
            print(f"  FAILED: {name} (exit {code})")
            ran.append((name, False))

        # Handle trigger cleanup
        trigger = config.get("trigger", "")
        if trigger.startswith("file_changed:") and code == 0:
            trigger_file = trigger.split(":", 1)[1]
            if os.path.exists(trigger_file):
                os.remove(trigger_file)

    save_state(state)
    print(f"Ran {len(ran)} harness(es)")
    return ran


def add_check(name, command, check_type="threshold", warn=80, critical=90,
              unit="", message=""):
    """Add a new check to the registry."""
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML required. Install: pip install pyyaml")
        return

    registry = load_registry()
    if name in registry.get("checks", {}):
        print(f"Check '{name}' already exists. Updating.")

    registry.setdefault("checks", {})[name] = {
        "enabled": True,
        "command": command,
        "type": check_type,
        "warn": warn,
        "critical": critical,
        "unit": unit,
        "message": message or f"{name}: {{value}}{unit}",
    }

    path = REGISTRY_PATH if os.path.exists(REGISTRY_PATH) else FALLBACK_REGISTRY
    with open(path, "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
    print(f"Added check: {name}")


def add_harness(name, script, window="offline", frequency="daily", description=""):
    """Add a new harness to the registry."""
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML required. Install: pip install pyyaml")
        return

    registry = load_registry()
    if name in registry.get("harnesses", {}):
        print(f"Harness '{name}' already exists. Updating.")

    registry.setdefault("harnesses", {})[name] = {
        "enabled": True,
        "script": script,
        "window": window,
        "frequency": frequency,
        "description": description or name,
    }

    path = REGISTRY_PATH if os.path.exists(REGISTRY_PATH) else FALLBACK_REGISTRY
    with open(path, "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
    print(f"Added harness: {name}")


def list_all():
    """List all registered checks and harnesses."""
    registry = load_registry()
    state = load_state()

    print("\n=== CHECKS ===")
    for name, config in registry.get("checks", {}).items():
        enabled = "ON " if config.get("enabled") else "OFF"
        check_type = config.get("type", "?")
        print(f"  [{enabled}] {name:<30} type={check_type}")

    print("\n=== HARNESSES ===")
    for name, config in registry.get("harnesses", {}).items():
        enabled = "ON " if config.get("enabled") else "OFF"
        window = config.get("window", "any")
        freq = config.get("frequency", "?")
        last = state.get(name, {}).get("last_run", "never")
        if last != "never":
            last = last[:16]  # Trim to datetime
        desc = config.get("description", "")[:40]
        print(f"  [{enabled}] {name:<25} {window:<8} {freq:<8} last={last}  {desc}")

    print()


def show_status():
    """Show current status of all components."""
    state = load_state()
    print("\n=== LAST RUN STATUS ===")
    for name, info in sorted(state.items()):
        last = info.get("last_run", "?")[:16]
        code = info.get("exit_code", "?")
        status = "OK" if code == 0 else f"FAIL({code})"
        print(f"  {name:<30} {status:<10} {last}")
    print()


def main():
    parser = argparse.ArgumentParser(description="AgentHarness Registry Engine")
    subparsers = parser.add_subparsers(dest="command")

    # run_checks
    p_checks = subparsers.add_parser("run_checks")
    p_checks.add_argument("--window", default="any")

    # run_harnesses
    p_harnesses = subparsers.add_parser("run_harnesses")
    p_harnesses.add_argument("--window", default="any")

    # add_check
    p_add_check = subparsers.add_parser("add_check")
    p_add_check.add_argument("name")
    p_add_check.add_argument("--command", required=True)
    p_add_check.add_argument("--type", default="threshold")
    p_add_check.add_argument("--warn", type=float, default=80)
    p_add_check.add_argument("--critical", type=float, default=90)
    p_add_check.add_argument("--unit", default="")
    p_add_check.add_argument("--message", default="")

    # add_harness
    p_add_harness = subparsers.add_parser("add_harness")
    p_add_harness.add_argument("name")
    p_add_harness.add_argument("--script", required=True)
    p_add_harness.add_argument("--window", default="offline")
    p_add_harness.add_argument("--frequency", default="daily")
    p_add_harness.add_argument("--description", default="")

    # list
    subparsers.add_parser("list")

    # status
    subparsers.add_parser("status")

    args = parser.parse_args()

    if args.command == "run_checks":
        run_checks(args.window)
    elif args.command == "run_harnesses":
        run_harnesses(args.window)
    elif args.command == "add_check":
        add_check(args.name, args.command, args.type, args.warn, args.critical,
                  args.unit, args.message)
    elif args.command == "add_harness":
        add_harness(args.name, args.script, args.window, args.frequency, args.description)
    elif args.command == "list":
        list_all()
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
