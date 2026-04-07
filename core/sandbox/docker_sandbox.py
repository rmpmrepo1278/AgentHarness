"""Containerized execution runner for untrusted scripts.

Runs scripts in ephemeral Docker containers with:
- No Docker socket access
- No host environment leaks
- Network disabled by default
- Memory and CPU limits
- Scripts mounted read-only
- Reports mounted read-write
- Auto-removed after execution

Uses the container_policy module from Phase A for argument generation,
but adds the execution layer on top.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Dict, List, Optional

from core.sandbox.direct import RunResult

log = logging.getLogger("sandbox.docker")

DEFAULT_IMAGE = "agentharness/sandbox:latest"
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1"
DEFAULT_TIMEOUT = 300


class ContainerRunner:
    """Execute scripts in ephemeral Docker containers.

    For community bundles and untrusted code. Full isolation.
    """

    def __init__(
        self,
        scripts_dir: str,
        reports_dir: str,
        image: str = DEFAULT_IMAGE,
    ):
        self.scripts_dir = scripts_dir
        self.reports_dir = reports_dir
        self.image = image

    def _build_command(
        self,
        script: str,
        args: List[str],
        allow_network: bool = False,
        memory: str = DEFAULT_MEMORY,
        cpus: str = DEFAULT_CPUS,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Build the docker run command with proper isolation.

        Returns a list of arguments for subprocess (not a shell string).
        """
        cmd = [
            "docker", "run",
            "--rm",
            f"--memory={memory}",
            f"--cpus={cpus}",
            "--pids-limit=256",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=100m",
        ]

        # Network isolation
        if allow_network:
            cmd.append("--network=bridge")
        else:
            cmd.append("--network=none")

        # Mount scripts read-only, reports read-write
        cmd.extend([
            "-v", f"{self.scripts_dir}:/scripts:ro",
            "-v", f"{self.reports_dir}:/reports:rw",
        ])

        # Explicit env vars only (no host env leak)
        if extra_env:
            for key, value in extra_env.items():
                cmd.extend(["-e", f"{key}={value}"])

        # Image and script
        cmd.append(self.image)
        cmd.append(f"/scripts/{script}")
        cmd.extend(args)

        return cmd

    def run(
        self,
        script: str,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        allow_network: bool = False,
        memory: str = DEFAULT_MEMORY,
        cpus: str = DEFAULT_CPUS,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        """Run a script in an ephemeral Docker container.

        Args:
            script: Script filename (relative to scripts_dir)
            args: Arguments to pass to the script
            timeout: Maximum execution time in seconds
            allow_network: Allow network access (default: disabled)
            memory: Memory limit (e.g., "512m", "1g")
            cpus: CPU limit (e.g., "1", "2")
            extra_env: Explicit environment variables to pass in
        """
        cmd = self._build_command(
            script, args,
            allow_network=allow_network,
            memory=memory,
            cpus=cpus,
            extra_env=extra_env,
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.warning("Container script %s timed out after %ds", script, timeout)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=f"Container timed out after {timeout}s",
                timed_out=True,
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
        except OSError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("Failed to run container for %s: %s", script, e)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
