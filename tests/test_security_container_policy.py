from __future__ import annotations

import pytest

from core.security.container_policy import build_docker_run_args


@pytest.fixture()
def default_args() -> list[str]:
    return build_docker_run_args(
        script="check.sh",
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )


def test_default_policy_has_no_network(default_args: list[str]) -> None:
    assert "--network=none" in default_args


def test_default_policy_has_memory_limit(default_args: list[str]) -> None:
    assert any(a.startswith("--memory=") for a in default_args)


def test_default_policy_has_no_docker_socket(default_args: list[str]) -> None:
    joined = " ".join(default_args)
    assert "docker.sock" not in joined


def test_no_host_env_leaked(default_args: list[str]) -> None:
    joined = " ".join(default_args)
    assert "--env-file" not in joined
    assert "GROQ_API_KEY" not in joined
    assert "TELEGRAM_BOT_TOKEN" not in joined


def test_network_opt_in() -> None:
    args = build_docker_run_args(
        script="fetch.sh",
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
        allow_network=True,
    )
    assert "--network=bridge" in args
    assert "--network=none" not in args


def test_scripts_mounted_readonly(default_args: list[str]) -> None:
    mounts = [a for a in default_args if a.startswith("-v") or a.startswith("--volume")]
    # The arg after -v flag or the --volume=... value
    all_mount_strs = []
    for i, a in enumerate(default_args):
        if a == "-v" and i + 1 < len(default_args):
            all_mount_strs.append(default_args[i + 1])
        elif a.startswith("-v") and ":" in a:
            all_mount_strs.append(a[2:])  # strip -v prefix
        elif a.startswith("--volume="):
            all_mount_strs.append(a[len("--volume="):])

    scripts_mount = [m for m in all_mount_strs if "/scripts" in m]
    assert scripts_mount, "No scripts mount found"
    assert any(":ro" in m for m in scripts_mount)


def test_reports_mounted_readwrite(default_args: list[str]) -> None:
    all_mount_strs = []
    for i, a in enumerate(default_args):
        if a == "-v" and i + 1 < len(default_args):
            all_mount_strs.append(default_args[i + 1])
        elif a.startswith("-v") and ":" in a:
            all_mount_strs.append(a[2:])
        elif a.startswith("--volume="):
            all_mount_strs.append(a[len("--volume="):])

    reports_mount = [m for m in all_mount_strs if "/reports" in m]
    assert reports_mount, "No reports mount found"
    assert any(":rw" in m for m in reports_mount)
