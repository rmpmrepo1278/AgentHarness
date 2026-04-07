"""Direct (host) execution runner for trusted scripts.

Runs scripts on the host with:
- Configurable timeout (kills on exceed)
- Stdout/stderr capture
- Duration tracking
- Environment variable passthrough

Used for shipped bundle scripts that we wrote and trust.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("sandbox.direct")

DEFAULT_TIMEOUT = 300  # seconds


@dataclass
class RunResult:
    """Result of a tool execution."""
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    sandbox_mode: str = "direct"


class DirectRunner:
    """Execute scripts directly on the host.

    Trusted scripts only. Enforces timeout but no other isolation.
    """

    def run(
        self,
        script: str,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> RunResult:
        """Run a script directly on the host.

        Args:
            script: Absolute path to the script
            args: Arguments to pass to the script
            timeout: Maximum execution time in seconds
            env: Additional environment variables (merged with current env)
            cwd: Working directory for the script
        """
        cmd = [script] + args
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=cwd,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.warning("Script %s timed out after %ds", script, timeout)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=f"Timed out after {timeout}s",
                timed_out=True,
                duration_ms=duration_ms,
            )
        except OSError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("Failed to execute %s: %s", script, e)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=duration_ms,
            )
