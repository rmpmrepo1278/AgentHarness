# tests/test_sandbox_runner.py
from __future__ import annotations
import os
import stat
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def scripts_dir(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture
def reports_dir(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    return d


def _make_script(scripts_dir, name, content):
    script = scripts_dir / name
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_dispatch_direct(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "check.sh", "#!/bin/bash\necho ok")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="check.sh",
        args=[],
        sandbox_mode="direct",
    )
    assert result.success is True
    assert result.sandbox_mode == "direct"
    assert "ok" in result.stdout


def test_dispatch_containerized_builds_docker_command(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    with patch("core.sandbox.docker_sandbox.subprocess") as mock_sub:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "container output"
        mock_proc.stderr = ""
        mock_sub.run.return_value = mock_proc

        result = runner.execute(
            script="check.sh",
            args=[],
            sandbox_mode="containerized",
        )
        assert result.success is True
        assert result.sandbox_mode == "containerized"
        # Verify docker was called
        mock_sub.run.assert_called_once()
        cmd = mock_sub.run.call_args[0][0]
        assert cmd[0] == "docker"


def test_invalid_sandbox_mode_raises(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner, InvalidSandboxMode
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    with pytest.raises(InvalidSandboxMode):
        runner.execute(script="x.sh", args=[], sandbox_mode="guarded")


def test_direct_uses_full_path(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "test.sh", "#!/bin/bash\necho ok")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="test.sh",
        args=[],
        sandbox_mode="direct",
    )
    assert result.success is True


def test_timeout_passthrough(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "slow.sh", "#!/bin/bash\nsleep 60")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="slow.sh",
        args=[],
        sandbox_mode="direct",
        timeout=1,
    )
    assert result.timed_out is True


def test_env_passthrough_direct(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "env.sh", '#!/bin/bash\necho "V=$MY_V"')
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="env.sh",
        args=[],
        sandbox_mode="direct",
        env={"MY_V": "42"},
    )
    assert "V=42" in result.stdout
