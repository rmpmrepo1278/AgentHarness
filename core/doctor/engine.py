"""RunbookExecutor — load YAML runbooks and execute self-healing steps.

Provides file-based locking (one runbook per service), sequential step
execution with branching (on_fail, on_known, on_unknown), snapshot
integration, LLM-assisted interpretation, and notification routing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.doctor.notify import NotificationRouter
from core.doctor.snapshot import SnapshotManager
from core.resilience.watchdog import recover_stale_lock

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of a single runbook step."""

    name: str
    action: str
    success: bool
    output: str = ""
    error: str = ""
    skipped: bool = False


@dataclass
class RunbookResult:
    """Result of executing a full runbook."""

    runbook: str
    trigger: str
    steps_executed: int
    steps_passed: int
    steps_failed: int
    result: str  # "pass", "fail", "escalated"
    fix_applied: bool
    snapshot_created: bool
    llm_used: bool
    duration_seconds: float
    notify_level: str
    step_results: list[StepResult] = field(default_factory=list)


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------

class RunbookExecutor:
    """Load and execute YAML runbooks for self-healing."""

    def __init__(
        self,
        data_dir: str,
        runbooks_dir: str,
        alert_script: str = "",
        chaguli_inbox_dir: str = "",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.runbooks_dir = Path(runbooks_dir)
        self.lock_dir = self.data_dir / "locks"

        self.snapshots = SnapshotManager(data_dir=data_dir)
        self.notifier = NotificationRouter(
            data_dir=data_dir,
            chaguli_inbox_dir=chaguli_inbox_dir or str(self.data_dir / "inbox"),
            alert_script=alert_script,
        )

        # Track state across an execution
        self._fix_applied = False
        self._snapshot_created = False
        self._llm_used = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_runbooks(self) -> list[dict[str, Any]]:
        """Return metadata for all YAML runbooks in runbooks_dir."""
        results: list[dict[str, Any]] = []
        if not self.runbooks_dir.is_dir():
            return results

        for yf in sorted(self.runbooks_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yf.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                results.append({
                    "file": str(yf),
                    "name": data.get("name", yf.stem),
                    "version": data.get("version", 1),
                    "trigger": data.get("trigger", ""),
                    "priority": data.get("priority", "medium"),
                    "notify": data.get("notify", "silent"),
                    "description": data.get("description", "").strip(),
                })
            except Exception as exc:
                log.warning("Failed to parse runbook %s: %s", yf, exc)
        return results

    def execute(
        self,
        runbook_name: str,
        trigger_context: str | None = None,
    ) -> RunbookResult:
        """Load and execute a runbook by name."""
        start = time.monotonic()
        self._fix_applied = False
        self._snapshot_created = False
        self._llm_used = False

        rb_path = self._find_runbook(runbook_name)
        if rb_path is None:
            return RunbookResult(
                runbook=runbook_name,
                trigger=trigger_context or "",
                steps_executed=0,
                steps_passed=0,
                steps_failed=0,
                result="fail",
                fix_applied=False,
                snapshot_created=False,
                llm_used=False,
                duration_seconds=0.0,
                notify_level="silent",
                step_results=[],
            )

        data = yaml.safe_load(rb_path.read_text(encoding="utf-8"))
        notify_level = data.get("notify", "silent")
        steps = data.get("steps", [])
        lock_name = data.get("name", runbook_name)

        if not self._acquire_lock(lock_name):
            return RunbookResult(
                runbook=runbook_name,
                trigger=trigger_context or "",
                steps_executed=0,
                steps_passed=0,
                steps_failed=0,
                result="fail",
                fix_applied=False,
                snapshot_created=False,
                llm_used=False,
                duration_seconds=time.monotonic() - start,
                notify_level="silent",
                step_results=[StepResult(
                    name="lock",
                    action="acquire",
                    success=False,
                    error="Could not acquire lock",
                )],
            )

        context: dict[str, Any] = {
            "runbook": runbook_name,
            "trigger": trigger_context or "",
        }
        step_results: list[StepResult] = []
        escalated = False

        try:
            for step in steps:
                sr = self.execute_step(step, context)
                step_results.append(sr)

                if not sr.success and not sr.skipped:
                    # Check for on_fail branching
                    if "on_fail" in step:
                        sub_results = self._handle_on_fail(
                            step, sr.output, context, dry_run=False,
                        )
                        step_results.extend(sub_results)
                        # If any sub-step escalated, mark it
                        if any(s.action == "escalate" and not s.success for s in sub_results):
                            escalated = True
                            break
                    else:
                        # No on_fail branch, just continue
                        pass

                # Handle interpret + on_known/on_unknown at check level
                if sr.success and step.get("interpret") == "llm":
                    sub_results = self._handle_llm_interpret(
                        step, sr.output, context, dry_run=False,
                    )
                    step_results.extend(sub_results)
                    if any(s.action == "escalate" and not s.success for s in sub_results):
                        escalated = True
                        break
        finally:
            self._release_lock(lock_name)

        passed = sum(1 for s in step_results if s.success and not s.skipped)
        failed = sum(1 for s in step_results if not s.success and not s.skipped)
        executed = sum(1 for s in step_results if not s.skipped)

        if escalated:
            result = "escalated"
        elif failed == 0:
            result = "pass"
        else:
            result = "fail"

        duration = time.monotonic() - start

        rb_result = RunbookResult(
            runbook=runbook_name,
            trigger=trigger_context or "",
            steps_executed=executed,
            steps_passed=passed,
            steps_failed=failed,
            result=result,
            fix_applied=self._fix_applied,
            snapshot_created=self._snapshot_created,
            llm_used=self._llm_used,
            duration_seconds=duration,
            notify_level=notify_level,
            step_results=step_results,
        )

        # Notify based on result
        title = f"Doctor: {runbook_name} [{result}]"
        body = (
            f"Executed {executed} step(s): {passed} passed, {failed} failed. "
            f"Duration: {duration:.1f}s. Fix applied: {self._fix_applied}."
        )
        self.notifier.notify(notify_level, title, body, runbook=runbook_name)

        # Log to doctor_log.jsonl
        self._log_result(rb_result)

        return rb_result

    def execute_step(
        self,
        step: dict[str, Any],
        context: dict[str, Any],
        dry_run: bool = False,
    ) -> StepResult:
        """Execute a single runbook step."""
        name = step.get("name", "unnamed")

        # Escalate step (may appear as top-level dict key)
        if "escalate" in step and "check" not in step and "fix" not in step:
            msg = step["escalate"]
            if dry_run:
                return StepResult(
                    name=name, action="escalate", success=True,
                    output=msg, skipped=True,
                )
            return StepResult(
                name=name, action="escalate", success=False,
                error=str(msg),
            )

        # Snapshot step
        if "snapshot" in step:
            if dry_run:
                return StepResult(
                    name=name, action="snapshot", success=True,
                    output="(dry run)", skipped=True,
                )
            try:
                snap_path = self.snapshots.snapshot(
                    step["snapshot"], runbook_name=context.get("runbook", "unknown"),
                )
                self._snapshot_created = True
                return StepResult(
                    name=name, action="snapshot", success=True,
                    output=snap_path,
                )
            except Exception as exc:
                return StepResult(
                    name=name, action="snapshot", success=False,
                    error=str(exc),
                )

        # Wait step
        if "wait" in step and "check" not in step and "fix" not in step:
            seconds = int(step["wait"])
            if dry_run:
                return StepResult(
                    name=name, action="wait", success=True,
                    output=f"sleep {seconds}s", skipped=True,
                )
            time.sleep(seconds)
            return StepResult(
                name=name, action="wait", success=True,
                output=f"slept {seconds}s",
            )

        # Check step
        if "check" in step:
            if dry_run:
                return StepResult(
                    name=name, action="check", success=True,
                    output="(dry run)", skipped=True,
                )
            exit_code, output = self._run_command(
                step["check"], timeout=step.get("timeout", 30),
            )
            passed = self._evaluate_check(step, exit_code, output)
            return StepResult(
                name=name, action="check", success=passed,
                output=output,
                error="" if passed else f"exit={exit_code}",
            )

        # Fix step
        if "fix" in step:
            self._fix_applied = True
            if dry_run:
                return StepResult(
                    name=name, action="fix", success=True,
                    output="(dry run)", skipped=True,
                )
            exit_code, output = self._run_command(
                step["fix"], timeout=step.get("timeout", 60),
            )
            sr = StepResult(
                name=name, action="fix", success=(exit_code == 0),
                output=output,
                error="" if exit_code == 0 else f"exit={exit_code}",
            )
            # Handle inline wait after fix
            if "wait" in step:
                time.sleep(int(step["wait"]))
            return sr

        return StepResult(
            name=name, action="unknown", success=False,
            error="Unrecognized step type",
        )

    def dry_run(self, runbook_name: str) -> list[StepResult]:
        """Walk through a runbook without executing commands."""
        rb_path = self._find_runbook(runbook_name)
        if rb_path is None:
            return [StepResult(
                name="load", action="load", success=False,
                error=f"Runbook not found: {runbook_name}",
            )]

        data = yaml.safe_load(rb_path.read_text(encoding="utf-8"))
        steps = data.get("steps", [])
        context: dict[str, Any] = {"runbook": runbook_name, "trigger": "dry_run"}
        results: list[StepResult] = []

        for step in steps:
            sr = self.execute_step(step, context, dry_run=True)
            results.append(sr)

            # Walk on_fail branches too
            if "on_fail" in step:
                sub = self._handle_on_fail(step, "", context, dry_run=True)
                results.extend(sub)

        return results

    # ------------------------------------------------------------------
    # Branching
    # ------------------------------------------------------------------

    def _handle_on_fail(
        self,
        step: dict[str, Any],
        output: str,
        context: dict[str, Any],
        dry_run: bool,
    ) -> list[StepResult]:
        """Process the on_fail branch of a step."""
        results: list[StepResult] = []
        for sub_step in step.get("on_fail", []):
            sr = self.execute_step(sub_step, context, dry_run=dry_run)
            results.append(sr)

            if not sr.success and not sr.skipped and not dry_run:
                # Recurse into nested on_fail
                if "on_fail" in sub_step:
                    results.extend(
                        self._handle_on_fail(sub_step, sr.output, context, dry_run)
                    )

            # Handle interpret on sub-step check results
            if sr.success and sub_step.get("interpret") == "llm" and not dry_run:
                results.extend(
                    self._handle_llm_interpret(sub_step, sr.output, context, dry_run)
                )
            elif not sr.success and sub_step.get("interpret") == "llm" and not dry_run:
                # The check itself may have failed (non-zero exit) but produced
                # output we still want to interpret
                results.extend(
                    self._handle_llm_interpret(sub_step, sr.output, context, dry_run)
                )

            # Handle inline wait
            if "wait" in sub_step and "check" in sub_step and not dry_run:
                time.sleep(int(sub_step["wait"]))

        return results

    def _handle_llm_interpret(
        self,
        step: dict[str, Any],
        output: str,
        context: dict[str, Any],
        dry_run: bool,
    ) -> list[StepResult]:
        """Check on_known patterns first, then LLM for on_unknown."""
        results: list[StepResult] = []
        on_known = step.get("on_known", {})
        on_unknown = step.get("on_unknown", {})

        # Check known patterns via substring match
        for pattern, action in on_known.items():
            if pattern in output:
                if isinstance(action, str) and action.startswith("escalate:"):
                    msg = action[len("escalate:"):]
                    results.append(StepResult(
                        name=f"{step.get('name', 'interpret')}-known",
                        action="escalate",
                        success=False,
                        error=msg,
                        skipped=dry_run,
                    ))
                elif isinstance(action, str) and action.startswith("runbook:"):
                    # Cross-reference to another runbook
                    results.append(StepResult(
                        name=f"{step.get('name', 'interpret')}-known",
                        action="runbook_ref",
                        success=True,
                        output=action,
                        skipped=dry_run,
                    ))
                return results

        # No known pattern matched -- try LLM or escalate
        if on_unknown.get("escalate"):
            if dry_run:
                results.append(StepResult(
                    name=f"{step.get('name', 'interpret')}-unknown",
                    action="escalate",
                    success=True,
                    output="(dry run: would escalate)",
                    skipped=True,
                ))
            else:
                # Try LLM first if available
                llm_response = self._call_llm(
                    f"Diagnose this service output:\n\n{output[:2000]}"
                )
                if llm_response:
                    self._llm_used = True
                    results.append(StepResult(
                        name=f"{step.get('name', 'interpret')}-llm",
                        action="llm_interpret",
                        success=False,
                        output=llm_response,
                        error="LLM could not match a known fix",
                    ))
                else:
                    results.append(StepResult(
                        name=f"{step.get('name', 'interpret')}-unknown",
                        action="escalate",
                        success=False,
                        error="Unknown error, escalated for manual review",
                    ))

        return results

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _run_command(self, command: str, timeout: int = 30) -> tuple[int, str]:
        """Run a shell command, return (exit_code, combined_output)."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (proc.stdout + proc.stderr).strip()
            return proc.returncode, output
        except subprocess.TimeoutExpired:
            return 124, f"Command timed out after {timeout}s"
        except Exception as exc:
            return 1, str(exc)

    def _evaluate_check(
        self,
        step: dict[str, Any],
        exit_code: int,
        output: str,
    ) -> bool:
        """Evaluate whether a check step passed."""
        # expect_exit_code
        if "expect_exit_code" in step:
            return exit_code == int(step["expect_exit_code"])

        # expect_contains — empty string always matches
        if "expect_contains" in step:
            expected = step["expect_contains"]
            if expected == "":
                # Empty expect_contains means output should be empty
                return output.strip() == ""
            return expected in output

        # expect_regex
        if "expect_regex" in step:
            return bool(re.search(step["expect_regex"], output))

        # Default: exit code 0
        return exit_code == 0

    # ------------------------------------------------------------------
    # LLM delegation
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """Delegate to AutoFixer._call_llm() if available."""
        try:
            from core.doctor.autofix import AutoFixer

            fixer = AutoFixer(data_dir=str(self.data_dir))
            response = fixer._call_llm(prompt)
            if response.success:
                return response.text
            return ""
        except Exception as exc:
            log.warning("LLM call failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # File-based locking
    # ------------------------------------------------------------------

    def _acquire_lock(self, name: str) -> bool:
        """Acquire a PID-based lock file. Returns True on success."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_dir / f"{name}.lock"

        if lock_file.exists():
            # Try to recover stale lock
            recovered = recover_stale_lock(lock_file)
            if not recovered:
                log.warning("Lock already held for %s", name)
                return False

        try:
            lock_file.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception as exc:
            log.error("Failed to write lock %s: %s", lock_file, exc)
            return False

    def _release_lock(self, name: str) -> None:
        """Release the lock file."""
        lock_file = self.lock_dir / f"{name}.lock"
        lock_file.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_runbook(self, name: str) -> Path | None:
        """Find a runbook YAML by name (with or without .yaml extension)."""
        candidates = [
            self.runbooks_dir / f"{name}.yaml",
            self.runbooks_dir / name,
        ]
        for c in candidates:
            if c.is_file():
                return c
        log.error("Runbook not found: %s (searched %s)", name, self.runbooks_dir)
        return None

    def _log_result(self, result: RunbookResult) -> None:
        """Append result summary to doctor_log.jsonl."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.data_dir / "doctor_log.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runbook": result.runbook,
            "trigger": result.trigger,
            "result": result.result,
            "steps_executed": result.steps_executed,
            "steps_passed": result.steps_passed,
            "steps_failed": result.steps_failed,
            "fix_applied": result.fix_applied,
            "snapshot_created": result.snapshot_created,
            "llm_used": result.llm_used,
            "duration_seconds": round(result.duration_seconds, 3),
            "notify_level": result.notify_level,
        }
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main() -> None:
    """CLI: python3 -m core.doctor.engine RUNBOOK [--dry-run] [--data-dir DIR] [--runbooks-dir DIR]"""
    import argparse

    parser = argparse.ArgumentParser(description="Run a doctor runbook")
    parser.add_argument("runbook", help="Runbook name (without .yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Walk through without executing")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--runbooks-dir", default="core/doctor/runbooks", help="Runbooks directory")
    parser.add_argument("--alert-script", default="", help="Path to alert.sh")
    parser.add_argument("--inbox-dir", default="", help="Chaguli inbox directory")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    executor = RunbookExecutor(
        data_dir=args.data_dir,
        runbooks_dir=args.runbooks_dir,
        alert_script=args.alert_script,
        chaguli_inbox_dir=args.inbox_dir,
    )

    if args.dry_run:
        results = executor.dry_run(args.runbook)
        for sr in results:
            status = "SKIP" if sr.skipped else ("OK" if sr.success else "FAIL")
            print(f"  [{status}] {sr.name}: {sr.action} -> {sr.output or sr.error}")
    else:
        result = executor.execute(args.runbook, trigger_context="cli")
        print(f"Runbook: {result.runbook}")
        print(f"Result:  {result.result}")
        print(f"Steps:   {result.steps_executed} executed, {result.steps_passed} passed, {result.steps_failed} failed")
        print(f"Duration: {result.duration_seconds:.1f}s")
        for sr in result.step_results:
            status = "SKIP" if sr.skipped else ("OK" if sr.success else "FAIL")
            print(f"  [{status}] {sr.name}: {sr.action} -> {sr.output[:80] if sr.output else sr.error[:80]}")


if __name__ == "__main__":
    main()
