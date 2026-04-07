from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


def test_build_command_default_isolation():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "docker" in joined
    assert "--rm" in cmd
    assert "--network=none" in cmd
    assert any("--memory=" in c for c in cmd)
    assert any("/opt/scripts" in c and ":ro" in c for c in cmd)
    assert any("/opt/reports" in c and ":rw" in c for c in cmd)


def test_build_command_network_opt_in():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[], allow_network=True)
    assert "--network=none" not in cmd
    assert "--network=bridge" in cmd


def test_build_command_custom_resources():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command(
        "heavy.sh", args=[], memory="1g", cpus="2",
    )
    assert "--memory=1g" in cmd
    assert "--cpus=2" in cmd


def test_build_command_includes_script_args():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=["--verbose", "--dry-run"])
    # Script and args should be at the end
    assert cmd[-3] == "/scripts/check.sh"
    assert cmd[-2] == "--verbose"
    assert cmd[-1] == "--dry-run"


def test_build_command_no_docker_socket():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "docker.sock" not in joined


def test_build_command_no_env_leak():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "--env-file" not in joined
    assert "GROQ_API_KEY" not in joined


@patch("core.sandbox.docker_sandbox.subprocess")
def test_run_success(mock_subprocess):
    from core.sandbox.docker_sandbox import ContainerRunner
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "output"
    mock_proc.stderr = ""
    mock_subprocess.run.return_value = mock_proc

    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    result = runner.run(script="check.sh", args=[])
    assert result.success is True
    assert result.sandbox_mode == "containerized"
    assert result.stdout == "output"


@patch("core.sandbox.docker_sandbox.subprocess")
def test_run_timeout(mock_subprocess):
    from core.sandbox.docker_sandbox import ContainerRunner
    import subprocess as real_subprocess
    mock_subprocess.run.side_effect = real_subprocess.TimeoutExpired(
        cmd=["docker"], timeout=10,
    )
    mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired

    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    result = runner.run(script="check.sh", args=[], timeout=10)
    assert result.success is False
    assert result.timed_out is True
    assert result.sandbox_mode == "containerized"
