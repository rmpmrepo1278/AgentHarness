from __future__ import annotations
import os
import stat
import pytest


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
    """Create an executable script in the scripts directory."""
    script = scripts_dir / name
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_direct_run_success(scripts_dir, reports_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "hello.sh", "#!/bin/bash\necho 'hello world'")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "hello.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is True
    assert result.exit_code == 0
    assert "hello world" in result.stdout


def test_direct_run_captures_stderr(scripts_dir, reports_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "warn.sh", "#!/bin/bash\necho 'warning' >&2\nexit 0")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "warn.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is True
    assert "warning" in result.stderr


def test_direct_run_nonzero_exit(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "fail.sh", "#!/bin/bash\nexit 42")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "fail.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is False
    assert result.exit_code == 42


def test_direct_run_timeout(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "slow.sh", "#!/bin/bash\nsleep 60")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "slow.sh"),
        args=[],
        timeout=1,
    )
    assert result.success is False
    assert result.timed_out is True


def test_direct_run_passes_args(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "args.sh", '#!/bin/bash\necho "arg1=$1 arg2=$2"')
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "args.sh"),
        args=["hello", "world"],
        timeout=10,
    )
    assert "arg1=hello arg2=world" in result.stdout


def test_direct_run_passes_env(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "env.sh", '#!/bin/bash\necho "VAR=$MY_VAR"')
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "env.sh"),
        args=[],
        timeout=10,
        env={"MY_VAR": "test_value"},
    )
    assert "VAR=test_value" in result.stdout


def test_run_result_duration(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "fast.sh", "#!/bin/bash\ntrue")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "fast.sh"),
        args=[],
        timeout=10,
    )
    assert result.duration_ms >= 0
    assert result.duration_ms < 5000  # Should be fast
