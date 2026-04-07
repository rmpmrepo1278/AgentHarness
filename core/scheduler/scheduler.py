"""Python scheduler — replaces scheduler.sh with registry/budget/heartbeat integration.

Runs one scheduler tick: heartbeat, network detection, check execution with
circuit-breaker evaluation, harness scheduling by window + frequency + last_run.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

import yaml

from core.discovery.state import StateManager
from core.registry.loader import load_registry
from core.resilience.atomic_json import atomic_write_json, safe_read_json
from core.resilience.circuit_breaker import CircuitBreaker
from core.resilience.watchdog import write_heartbeat
from core.scheduler.windows import get_network_state, get_window, is_task_due

logger = logging.getLogger(__name__)

_CHECK_TIMEOUT = 30  # seconds


class Scheduler:
    """One-shot scheduler that executes checks and harnesses per tick."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._state_mgr = StateManager(data_dir)
        self._cb = CircuitBreaker(data_dir)
        self._sched_state_path = self._data_dir / "scheduler_state.json"

    def tick(self) -> dict:
        """Execute one scheduler tick and return a summary dict."""
        # 1. Write heartbeat
        write_heartbeat(self._data_dir)

        # 2. Detect network state + window
        network_state = get_network_state()
        window = get_window(network_state)

        # 3. Load registry from bundles
        state = self._state_mgr.read()
        bundles_dir = state.get("paths", {}).get("bundles_dir", "")
        scripts_dir = state.get("paths", {}).get("scripts_dir", "")

        registry: Dict[str, Any] = {"checks": {}, "harnesses": {}}
        if bundles_dir and Path(bundles_dir).is_dir():
            registry = load_registry(bundles_dir)

        # 4. Run enabled checks
        checks_run = 0
        checks_passed = 0
        checks_failed = 0

        for name, check in registry.get("checks", {}).items():
            if not check.get("enabled", True):
                continue
            if self._cb.is_open(name):
                logger.info("Circuit open for %s — skipping", name)
                continue

            command = check.get("command", "")
            if not command:
                continue

            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=_CHECK_TIMEOUT,
                )
                output = result.stdout.strip()
                returncode = result.returncode
            except subprocess.TimeoutExpired:
                output = ""
                returncode = -1

            checks_run += 1
            check_type = check.get("type", "command_exit")
            passed = self._evaluate_check(check_type, check, output, returncode)

            if passed:
                checks_passed += 1
                self._cb.record_success(name)
            else:
                checks_failed += 1
                self._cb.record_failure(name)

        # 5. Run due harnesses (check window + frequency + last_run)
        sched_state = safe_read_json(self._sched_state_path, default={})
        if not isinstance(sched_state, dict):
            sched_state = {}
        harness_last_runs = sched_state.get("harness_last_runs", {})

        harnesses_run = 0

        for name, harness in registry.get("harnesses", {}).items():
            if not harness.get("enabled", True):
                continue

            # Window check: "any" matches all windows
            harness_window = harness.get("window", "any")
            if harness_window != "any" and harness_window != window:
                continue

            # Frequency / due check
            frequency = harness.get("frequency", "1h")
            last_run = harness_last_runs.get(name, 0.0)
            if not is_task_due(frequency, last_run):
                continue

            script = harness.get("script", "")
            if not script:
                continue

            script_path = Path(scripts_dir) / script if scripts_dir else Path(script)
            if not script_path.is_file():
                logger.warning("Harness script not found: %s", script_path)
                continue

            try:
                subprocess.run(
                    ["bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=_CHECK_TIMEOUT,
                )
                harnesses_run += 1
                harness_last_runs[name] = time.time()
            except subprocess.TimeoutExpired:
                logger.warning("Harness %s timed out", name)
            except OSError as exc:
                logger.warning("Harness %s failed: %s", name, exc)

        # 6. Save harness state
        sched_state["harness_last_runs"] = harness_last_runs
        atomic_write_json(self._sched_state_path, sched_state)

        # 7. Return summary
        return {
            "network_state": network_state,
            "window": window,
            "checks_run": checks_run,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "harnesses_run": harnesses_run,
        }

    def _evaluate_check(
        self, check_type: str, check: dict, output: str, returncode: int
    ) -> bool:
        """Evaluate a check result based on its type.

        Returns True if the check passes, False if it fails/alerts.
        """
        if check_type == "threshold":
            try:
                value = float(output)
            except (ValueError, TypeError):
                return False
            critical = check.get("critical")
            if critical is not None and value >= float(critical):
                return False
            warn = check.get("warn")
            if warn is not None and value >= float(warn):
                return False
            return True

        if check_type == "command_exit":
            return returncode == 0

        if check_type == "command_output":
            # Alert if non-empty output
            return len(output) == 0

        if check_type == "http_probe":
            return returncode == 0

        if check_type == "regex_match":
            expected = check.get("expected", "")
            return bool(re.search(expected, output))

        # Unknown type — treat as command_exit
        return returncode == 0


def main() -> None:
    """CLI entry point: python3 -m core.scheduler.scheduler --data-dir X."""
    parser = argparse.ArgumentParser(description="AgentHarness Scheduler")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Path to the AgentHarness data directory",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    scheduler = Scheduler(args.data_dir)
    summary = scheduler.tick()
    logger.info("Tick complete: %s", summary)


if __name__ == "__main__":
    main()
