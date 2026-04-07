"""Unified sandbox runner -- dispatches to direct or containerized execution.

Reads the tool's configured sandbox_mode and routes execution to the
appropriate runner. Only two modes exist:
- direct: host execution for trusted shipped bundles
- containerized: Docker execution for community/untrusted code

The old "guarded" mode (regex-blocking) was dropped as a fake sandbox.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.sandbox.direct import DirectRunner, RunResult
from core.sandbox.docker_sandbox import ContainerRunner

log = logging.getLogger("sandbox.runner")

VALID_MODES = {"direct", "containerized"}


class InvalidSandboxMode(Exception):
    pass


class SandboxRunner:
    """Dispatch tool execution to the correct sandbox mode.

    Usage:
        runner = SandboxRunner(scripts_dir="/opt/ah/scripts", reports_dir="/opt/ah/reports")
        result = runner.execute(script="check.sh", args=[], sandbox_mode="direct")
    """

    def __init__(
        self,
        scripts_dir: str,
        reports_dir: str,
        docker_image: str = "agentharness/sandbox:latest",
    ):
        self.scripts_dir = Path(scripts_dir)
        self.reports_dir = Path(reports_dir)
        self._direct = DirectRunner()
        self._container = ContainerRunner(
            scripts_dir=str(self.scripts_dir),
            reports_dir=str(self.reports_dir),
            image=docker_image,
        )

    def execute(
        self,
        script: str,
        args: List[str],
        sandbox_mode: str,
        timeout: int = 300,
        env: Optional[Dict[str, str]] = None,
        allow_network: bool = False,
        memory: str = "512m",
        cpus: str = "1",
    ) -> RunResult:
        """Execute a tool script in the configured sandbox mode.

        Args:
            script: Script filename (relative to scripts_dir)
            args: Arguments to pass to the script
            sandbox_mode: "direct" or "containerized"
            timeout: Maximum execution time in seconds
            env: Environment variables to pass through
            allow_network: (containerized only) Allow network access
            memory: (containerized only) Memory limit
            cpus: (containerized only) CPU limit
        """
        if sandbox_mode not in VALID_MODES:
            raise InvalidSandboxMode(
                f"Invalid sandbox mode {sandbox_mode!r}. Valid modes: {VALID_MODES}"
            )

        log.info(
            "Executing %s in %s mode (timeout=%ds)",
            script, sandbox_mode, timeout,
        )

        if sandbox_mode == "direct":
            script_path = str(self.scripts_dir / script)
            return self._direct.run(
                script=script_path,
                args=args,
                timeout=timeout,
                env=env,
            )
        else:
            return self._container.run(
                script=script,
                args=args,
                timeout=timeout,
                allow_network=allow_network,
                memory=memory,
                cpus=cpus,
                extra_env=env,
            )
