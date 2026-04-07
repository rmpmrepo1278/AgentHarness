from __future__ import annotations

from typing import Dict, List, Optional


def build_docker_run_args(
    script: str,
    scripts_dir: str,
    reports_dir: str,
    image: str = "agentharness/sandbox:latest",
    allow_network: bool = False,
    memory: str = "512m",
    cpus: str = "1",
    timeout: int = 300,
    extra_mounts: Optional[List[str]] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> list[str]:
    """Build docker run args with secure defaults.

    Returns a list of strings suitable for subprocess.run() — never a shell
    string, which would be vulnerable to injection.
    """
    args: list[str] = ["docker", "run", "--rm"]

    # Resource limits
    args.append(f"--memory={memory}")
    args.append(f"--cpus={cpus}")
    args.append("--pids-limit=256")

    # Filesystem
    args.append("--read-only")
    args.append("--tmpfs=/tmp:rw,noexec,nosuid,size=100m")

    # Privilege reduction
    args.append("--no-new-privileges")
    args.append("--security-opt=no-new-privileges")

    # Network isolation
    if allow_network:
        args.append("--network=bridge")
    else:
        args.append("--network=none")

    # Mounts — scripts readonly, reports read-write
    args.extend(["-v", f"{scripts_dir}:/scripts:ro"])
    args.extend(["-v", f"{reports_dir}:/reports:rw"])

    if extra_mounts:
        for mount in extra_mounts:
            args.extend(["-v", mount])

    # Environment — only safe defaults, NO host env passthrough
    args.extend(["-e", "TERM=dumb"])
    args.extend(["-e", "HOME=/tmp"])

    if extra_env:
        for key, value in extra_env.items():
            args.extend(["-e", f"{key}={value}"])

    # Timeout via docker stop deadline
    args.append(f"--stop-timeout={timeout}")

    # Image + command
    args.append(image)
    args.extend(["bash", f"/scripts/{script}"])

    return args
