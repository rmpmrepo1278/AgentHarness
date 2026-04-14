"""Guided troubleshooter — deterministic rule-based remediation.

Runs selftest + additional checks (circuit breakers, integrity, heartbeat,
providers), identifies issues, and returns step-by-step fix instructions
for each one.  No LLM involved — pure deterministic rule matching.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class FixStep:
    description: str      # "Change ownership of the reports directory"
    command: str           # "sudo chown $USER:$USER /path/to/reports"
    verify: str            # "ls -la /path/to/reports"


@dataclass
class Issue:
    name: str              # "reports_dir_not_writable"
    severity: str          # "critical" | "warning" | "info"
    description: str       # "The reports directory is not writable"
    root_cause: str        # "Directory owned by root, AgentHarness runs as user"
    fix_steps: list        # list[FixStep]  (Python 3.9 compat)


class Troubleshooter:
    """Detect issues and generate fix steps for each."""

    # How old a heartbeat can be before we flag the scheduler (seconds).
    _HEARTBEAT_MAX_AGE = 1800  # 30 minutes

    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir
        self._state: Dict[str, Any] = {}
        self._install_dir: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        """Load state.json if it exists."""
        path = os.path.join(self._data_dir, "state.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    # ------------------------------------------------------------------
    # Individual issue detectors
    # ------------------------------------------------------------------

    def _check_state_file(self) -> Optional[Issue]:
        path = os.path.join(self._data_dir, "state.json")
        if not os.path.isfile(path):
            return Issue(
                name="state_file_missing",
                severity="critical",
                description="state.json is missing — AgentHarness has not been discovered yet.",
                root_cause="Discovery has not been run, or the data directory is incorrect.",
                fix_steps=[
                    FixStep(
                        description="Run discovery to generate state.json",
                        command="python3 cli.py discover",
                        verify=f"ls -la {path}",
                    ),
                ],
            )
        # Try parsing
        try:
            with open(path, "r") as fh:
                json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            return Issue(
                name="state_file_corrupt",
                severity="critical",
                description=f"state.json exists but is corrupt: {exc}",
                root_cause="File was partially written or manually edited with errors.",
                fix_steps=[
                    FixStep(
                        description="Re-run discovery to regenerate state.json",
                        command="python3 cli.py discover",
                        verify=f"python3 -c \"import json; json.load(open('{path}'))\"",
                    ),
                ],
            )
        return None

    def _check_dir_writable(self, label: str, dir_key: str) -> Optional[Issue]:
        path = self._state.get(dir_key, "")
        if not path:
            return None  # Can't check without state
        if not os.path.isdir(path):
            return Issue(
                name=f"{label}_not_writable",
                severity="critical",
                description=f"The {label} directory does not exist: {path}",
                root_cause="Directory was never created, or was deleted.",
                fix_steps=[
                    FixStep(
                        description=f"Create the {label} directory",
                        command=f"mkdir -p {path}",
                        verify=f"ls -la {path}",
                    ),
                ],
            )
        # Check writability
        import tempfile
        try:
            fd, tmp = tempfile.mkstemp(dir=path, prefix=".troubleshoot_")
            os.close(fd)
            os.unlink(tmp)
        except OSError:
            return Issue(
                name=f"{label}_not_writable",
                severity="critical",
                description=f"The {label} directory is not writable: {path}",
                root_cause="Directory owned by root or permissions are restrictive.",
                fix_steps=[
                    FixStep(
                        description=f"Change ownership of the {label} directory",
                        command=f"sudo chown $USER:$USER {path}",
                        verify=f"ls -la {path}",
                    ),
                ],
            )
        return None

    def _check_docker(self) -> Optional[Issue]:
        if shutil.which("docker") is None:
            return Issue(
                name="docker_not_available",
                severity="warning",
                description="Docker is not installed or not in PATH.",
                root_cause="Docker is not installed on this system.",
                fix_steps=[
                    FixStep(
                        description="Install Docker and add your user to the docker group",
                        command="sudo apt install docker.io && sudo usermod -aG docker $USER && newgrp docker",
                        verify="docker info",
                    ),
                ],
            )
        return None

    def _check_python_version(self) -> Optional[Issue]:
        if sys.version_info < (3, 9):
            return Issue(
                name="python_version_too_old",
                severity="critical",
                description=f"Python {sys.version_info.major}.{sys.version_info.minor} is too old (need >= 3.9).",
                root_cause="System Python is outdated.",
                fix_steps=[
                    FixStep(
                        description="Install Python 3.9+ via deadsnakes PPA",
                        command="sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.9",
                        verify="python3.9 --version",
                    ),
                ],
            )
        return None

    def _check_pip(self) -> Optional[Issue]:
        if shutil.which("pip3") is None and shutil.which("pip") is None:
            return Issue(
                name="pip_not_available",
                severity="warning",
                description="pip is not installed or not in PATH.",
                root_cause="pip was not installed with Python.",
                fix_steps=[
                    FixStep(
                        description="Install pip",
                        command="sudo apt install python3-pip",
                        verify="pip3 --version",
                    ),
                ],
            )
        return None

    def _check_pyyaml(self) -> Optional[Issue]:
        try:
            import yaml  # noqa: F401
        except ImportError:
            return Issue(
                name="pyyaml_not_importable",
                severity="warning",
                description="PyYAML is not installed — required for bundle loading.",
                root_cause="The pyyaml package is not installed in the current Python environment.",
                fix_steps=[
                    FixStep(
                        description="Install PyYAML",
                        command="pip3 install pyyaml",
                        verify="python3 -c 'import yaml; print(yaml.__version__)'",
                    ),
                ],
            )
        return None

    def _check_stale_locks(self) -> Optional[Issue]:
        lock_files = glob.glob(os.path.join(self._data_dir, "*.lock"))
        stale: List[str] = []
        for lock_path in lock_files:
            try:
                with open(lock_path, "r") as fh:
                    content = fh.read().strip()
                pid = int(content)
            except (ValueError, OSError):
                stale.append(lock_path)
                continue
            if not self._pid_alive(pid):
                stale.append(lock_path)
        if stale:
            stale_list = " ".join(stale)
            return Issue(
                name="stale_locks_detected",
                severity="warning",
                description=f"Stale lock files found: {stale_list}",
                root_cause="A previous AgentHarness process crashed without cleaning up its locks.",
                fix_steps=[
                    FixStep(
                        description="Remove stale lock files",
                        command=f"rm {stale_list}",
                        verify=f"ls {self._data_dir}/*.lock 2>/dev/null || echo 'No lock files'",
                    ),
                    FixStep(
                        description="Or run discovery to auto-recover",
                        command="python3 cli.py discover",
                        verify="python3 cli.py selftest",
                    ),
                ],
            )
        return None

    def _check_integrity(self) -> Optional[Issue]:
        install_dir = self._state.get("paths", {}).get("install_dir", "")
        if not install_dir:
            return None

        manifest_path = os.path.join(install_dir, "data", "integrity_manifest.json")
        if not os.path.isfile(manifest_path):
            return None  # No manifest to check against

        try:
            from core.security.integrity import verify_integrity
            result = verify_integrity(install_dir, manifest_path)
        except Exception:
            return None

        if result.get("status") in ("modified", "missing"):
            modified = result.get("modified", [])
            missing = result.get("missing", [])
            details: List[str] = []
            if modified:
                details.append(f"Modified: {', '.join(modified[:5])}")
            if missing:
                details.append(f"Missing: {', '.join(missing[:5])}")
            return Issue(
                name="integrity_modified",
                severity="warning",
                description=f"File integrity check failed. {'; '.join(details)}",
                root_cause="Files were modified outside AgentHarness.",
                fix_steps=[
                    FixStep(
                        description="Regenerate the integrity manifest if changes are intentional",
                        command="python3 cli.py discover",
                        verify="python3 cli.py integrity",
                    ),
                    FixStep(
                        description="Or investigate the changes manually",
                        command=f"python3 cli.py integrity",
                        verify="Review the output for unexpected modifications",
                    ),
                ],
            )
        return None

    def _check_circuit_breakers(self) -> Optional[Issue]:
        cb_path = os.path.join(self._data_dir, "circuit_breaker.json")
        if not os.path.isfile(cb_path):
            return None
        try:
            with open(cb_path, "r") as fh:
                cb_state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

        # Default max_failures is 5
        open_circuits = [name for name, count in cb_state.items() if count >= 5]
        if open_circuits:
            circuit_list = ", ".join(open_circuits)
            return Issue(
                name="circuit_breakers_open",
                severity="warning",
                description=f"Circuit breakers are open for: {circuit_list}",
                root_cause="These checks have failed repeatedly and are being suppressed.",
                fix_steps=[
                    FixStep(
                        description="Reset circuit breakers by running discovery",
                        command="python3 cli.py discover",
                        verify="python3 cli.py circuits",
                    ),
                    FixStep(
                        description="Or investigate why the checks are failing",
                        command="python3 cli.py selftest",
                        verify="Check the output for failures related to the suppressed checks",
                    ),
                ],
            )
        return None

    def _check_scheduler_heartbeat(self) -> Optional[Issue]:
        hb_path = os.path.join(self._data_dir, "heartbeat.json")
        if not os.path.isfile(hb_path):
            return Issue(
                name="scheduler_not_running",
                severity="warning",
                description="No heartbeat file found — the scheduler may not be running.",
                root_cause="The scheduler has never run, or heartbeat.json was deleted.",
                fix_steps=[
                    FixStep(
                        description="Check if systemd scheduler is active",
                        command="systemctl --user status agentharness-scheduler 2>/dev/null || echo 'systemd service not found'",
                        verify="systemctl --user is-active agentharness-scheduler 2>/dev/null || echo 'inactive'",
                    ),
                    FixStep(
                        description="Check if cron is set up as a fallback",
                        command="crontab -l 2>/dev/null | grep -i agentharness || echo 'No cron entry found'",
                        verify="crontab -l 2>/dev/null | grep -i agentharness",
                    ),
                    FixStep(
                        description="Start the scheduler manually",
                        command="systemctl --user start agentharness-scheduler || python3 cli.py migrate-scheduler",
                        verify="python3 cli.py selftest",
                    ),
                ],
            )

        try:
            with open(hb_path, "r") as fh:
                heartbeat = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

        ts = heartbeat.get("timestamp", 0)
        age = time.time() - ts
        if age > self._HEARTBEAT_MAX_AGE:
            minutes_ago = int(age / 60)
            return Issue(
                name="scheduler_not_running",
                severity="warning",
                description=f"Scheduler heartbeat is {minutes_ago} minutes old — it may have stopped.",
                root_cause="The scheduler process crashed or was stopped.",
                fix_steps=[
                    FixStep(
                        description="Check systemd scheduler status",
                        command="systemctl --user status agentharness-scheduler",
                        verify="systemctl --user is-active agentharness-scheduler",
                    ),
                    FixStep(
                        description="Restart the scheduler",
                        command="systemctl --user restart agentharness-scheduler",
                        verify=f"cat {hb_path}",
                    ),
                ],
            )
        return None

    def _check_systemd(self) -> Optional[Issue]:
        if shutil.which("systemctl") is None:
            return Issue(
                name="systemd_not_available",
                severity="info",
                description="systemd is not available — use cron as a fallback scheduler.",
                root_cause="This system does not use systemd (e.g., WSL1, containers, macOS).",
                fix_steps=[
                    FixStep(
                        description="Set up cron as a fallback scheduler",
                        command='(crontab -l 2>/dev/null; echo "*/15 * * * * cd {install} && python3 cli.py selftest") | crontab -'.format(
                            install=self._install_dir or "/path/to/AgentHarness"
                        ),
                        verify="crontab -l | grep agentharness",
                    ),
                ],
            )
        return None

    def _check_llm_providers(self) -> Optional[Issue]:
        has_groq = bool(os.environ.get("GROQ_API_KEY", ""))
        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
        has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY", ""))
        has_cerebras = bool(os.environ.get("CEREBRAS_API_KEY", ""))
        has_sambanova = bool(os.environ.get("SAMBANOVA_API_KEY", ""))
        has_google = bool(os.environ.get("GOOGLE_API_KEY", ""))
        has_ollama_cloud = bool(os.environ.get("OLLAMA_API_KEY", ""))

        has_any_cloud = any([
            has_groq, has_anthropic, has_openrouter,
            has_cerebras, has_sambanova, has_google, has_ollama_cloud,
        ])

        # Check for local LLM servers in state
        llm_servers = self._state.get("services", {}).get("llm_servers", [])
        has_local = len(llm_servers) > 0

        if not has_any_cloud and not has_local:
            return Issue(
                name="no_llm_providers",
                severity="info",
                description="No LLM providers detected — AgentHarness can operate without them but smart features are disabled.",
                root_cause="No API keys are set and no local LLM servers were discovered.",
                fix_steps=[
                    FixStep(
                        description="Set GROQ_API_KEY for free cloud LLM access",
                        command="export GROQ_API_KEY='your-key-here'  # Add to ~/.bashrc for persistence",
                        verify="echo $GROQ_API_KEY",
                    ),
                    FixStep(
                        description="Or start a local LLM server on port 8080",
                        command="# Start ik_llama.cpp or similar on port 8080, then re-run discover",
                        verify="curl -s http://localhost:8080/health || echo 'No local server running'",
                    ),
                ],
            )
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> list:
        """Detect all issues and generate fix steps for each."""
        self._state = self._load_state()
        self._install_dir = self._state.get("paths", {}).get("install_dir", "")

        issues: List[Issue] = []

        # Order: critical checks first, then warnings, then info
        detectors = [
            self._check_state_file,
            lambda: self._check_dir_writable("reports_dir", "reports_dir"),
            lambda: self._check_dir_writable("logs_dir", "logs_dir"),
            self._check_python_version,
            self._check_docker,
            self._check_systemd,
            self._check_pip,
            self._check_pyyaml,
            self._check_stale_locks,
            self._check_integrity,
            self._check_circuit_breakers,
            self._check_scheduler_heartbeat,
            self._check_llm_providers,
        ]

        for detector in detectors:
            try:
                result = detector()
                if result is not None:
                    issues.append(result)
            except Exception:
                # Don't let one broken detector stop the whole run
                pass

        return issues

    def format_guide(self, issues: list) -> str:
        """Format as a numbered walkthrough the user can follow."""
        if not issues:
            return "No issues detected. System is healthy."

        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  AgentHarness Troubleshooting Guide")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Found {len(issues)} issue(s):")
        lines.append("")

        issue_num = 0
        for issue in issues:
            issue_num += 1
            severity_tag = issue.severity.upper()
            lines.append(f"--- Issue {issue_num} [{severity_tag}]: {issue.description} ---")
            lines.append(f"    Root cause: {issue.root_cause}")
            lines.append("")

            for step_idx, step in enumerate(issue.fix_steps, 1):
                lines.append(f"    Step {step_idx}: {step.description}")
                lines.append(f"      Run:    {step.command}")
                lines.append(f"      Verify: {step.verify}")
                lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)
