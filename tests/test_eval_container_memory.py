"""Eval: container memory pressure and resource limits.

Verifies that every running container reports stats, stays inside its memory
limit (critical 95% / warning 85%), that known services (ollama, neo4j and
the metronix stack) sit within their documented budgets, that every enforced
limit is documented in a compose file, and that nothing was OOM-killed in
the last 24 hours.

All failure messages include the observed values for debugging.

Run on the homelab:  pytest tests/test_eval_container_memory.py -v
"""

import os
import re
import subprocess
import time
from functools import cache

import pytest

CRITICAL_THRESHOLD_PCT = 95.0
WARNING_THRESHOLD_PCT = 85.0

# Incident reference: ollama sat at 97% of a 12GiB limit before it was raised.
OLLAMA_LIMIT_BYTES = 16 * 1024**3
OLLAMA_MAX_EXPECTED_PCT = 97.0
NEO4J_LIMIT_BYTES = 1 * 1024**3

METRONIX_SERVICE_LIMITS = {
    "postgres": 512 * 1024**2,
    "qdrant": 512 * 1024**2,
    "redis": 256 * 1024**2,
    "api": 1 * 1024**3,
}

COMPOSE_WORKDIR_LABEL = "com.docker.compose.project.working_dir"
COMPOSE_FILES_LABEL = "com.docker.compose.project.config_files"

SIZE_UNITS = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
}


def _run_docker(*args, timeout=60):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="session")
def require_docker():
    probe = _run_docker("info", timeout=30)
    if probe.returncode != 0:
        pytest.skip(
            "docker is not available on this host: "
            f"{probe.stderr.strip() or probe.stdout.strip()}"
        )


@cache
def _running_containers():
    result = _run_docker("ps", "--format", "{{.Names}}")
    assert result.returncode == 0, f"docker ps failed: {result.stderr.strip()}"
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _parse_size(text):
    text = text.strip()
    match = re.fullmatch(r"([\d.]+)\s*([KMGTP]?i?B)", text)
    if not match:
        raise ValueError(f"unparseable size from docker stats: {text!r}")
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2)])


def _container_stats():
    """Return {name: {"memperc": float, "used": int, "limit": int|None}}."""
    fmt = "{{.Name}}|{{.MemPerc}}|{{.MemUsage}}|{{.MemUsage/Limit}}"
    result = _run_docker("stats", "--no-stream", "--format", fmt)
    if result.returncode != 0:
        # .MemUsage/Limit is rejected by older docker versions; MemUsage
        # already carries "used / limit".
        fmt = "{{.Name}}|{{.MemPerc}}|{{.MemUsage}}"
        result = _run_docker("stats", "--no-stream", "--format", fmt)
    assert result.returncode == 0, f"docker stats failed: {result.stderr.strip()}"

    stats = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        name, memperc, memusage = parts[0], parts[1], parts[2]
        used_raw, _, limit_raw = memusage.partition("/")
        stats[name] = {
            "memperc": float(memperc.rstrip("%") or 0),
            "used": _parse_size(used_raw),
            "limit": _parse_size(limit_raw) if limit_raw.strip() else None,
        }
    return stats


@cache
def _memory_limit(name):
    result = _run_docker("inspect", name, "--format", "{{.HostConfig.Memory}}")
    assert result.returncode == 0, (
        f"docker inspect {name} failed: {result.stderr.strip()}"
    )
    return int(result.stdout.strip())


def _compose_labels(name):
    fmt = '{{index .Config.Labels "%s"}}|{{index .Config.Labels "%s"}}' % (
        COMPOSE_WORKDIR_LABEL,
        COMPOSE_FILES_LABEL,
    )
    result = _run_docker("inspect", name, "--format", fmt)
    assert result.returncode == 0, (
        f"docker inspect {name} failed: {result.stderr.strip()}"
    )
    workdir, _, files = result.stdout.strip().partition("|")
    compose_files = [f.strip() for f in files.split(",") if f.strip()]
    return workdir.strip(), compose_files


def _compose_file_text(workdir, compose_files):
    chunks = []
    for entry in compose_files:
        path = entry if os.path.isabs(entry) else os.path.join(workdir, entry)
        try:
            with open(path, encoding="utf-8") as handle:
                chunks.append(handle.read())
        except OSError:
            continue
    return "\n".join(chunks)


def _size_tokens(limit):
    """Plausible textual spellings of a byte limit, as written in compose files."""
    tokens = {str(limit)}
    divisors = ((2**30, "g"), (10**9, "g"), (2**20, "m"), (10**6, "m"))
    for amount, unit in divisors:
        if limit % amount == 0:
            count = limit // amount
            tokens.update((f"{count}{unit}", f"{count}{unit}b", f"{count}{unit}ib"))
    return sorted(tokens)


def _token_present(text_lower, token):
    pattern = r"(?<![0-9a-z])" + re.escape(token) + r"(?![0-9a-z])"
    return re.search(pattern, text_lower) is not None


def _fmt_bytes(num):
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{num}B"


def _find_container(stats, needle):
    if needle in stats:
        return needle
    matches = [name for name in sorted(stats) if needle in name.lower()]
    return matches[0] if matches else None


def _find_metronix_container(stats, service):
    exact = f"metronix-{service}"
    if exact in stats:
        return exact
    matches = [
        name
        for name in sorted(stats)
        if "metronix" in name.lower() and service in name.lower()
    ]
    return matches[0] if matches else None


def _threshold_violations(threshold_pct):
    stats = _container_stats()
    violations = []
    for name in sorted(stats):
        limit = _memory_limit(name)
        if limit <= 0:
            continue
        used = stats[name]["used"]
        pct = used / limit * 100
        if pct > threshold_pct:
            violations.append(
                f"{name}: {pct:.1f}% of limit "
                f"({_fmt_bytes(used)}/{_fmt_bytes(limit)})"
            )
    return violations


def test_docker_stats_returns_data_for_all_running_containers(require_docker):
    containers = _running_containers()
    assert containers, "docker ps reported no running containers"
    stats = _container_stats()

    missing = sorted(set(containers) - set(stats))
    unexpected = sorted(set(stats) - set(containers))
    assert not missing and not unexpected, (
        f"docker stats mismatch: {len(missing)} missing out of "
        f"{len(containers)} running containers={missing}, "
        f"unexpected entries={unexpected}"
    )


def test_no_container_over_critical_memory_threshold(require_docker):
    violations = _threshold_violations(CRITICAL_THRESHOLD_PCT)
    assert not violations, (
        f"{len(violations)} container(s) above {CRITICAL_THRESHOLD_PCT}% of "
        "their memory limit:\n" + "\n".join(violations)
    )


def test_no_container_over_warning_memory_threshold(require_docker):
    violations = _threshold_violations(WARNING_THRESHOLD_PCT)
    assert not violations, (
        f"{len(violations)} container(s) above {WARNING_THRESHOLD_PCT}% of "
        "their memory limit:\n" + "\n".join(violations)
    )


def test_ollama_within_16gb_limit(require_docker):
    stats = _container_stats()
    name = _find_container(stats, "ollama")
    assert name, f"no ollama container found; running containers: {sorted(stats)}"

    limit = _memory_limit(name)
    assert limit == OLLAMA_LIMIT_BYTES, (
        f"{name} memory limit is {_fmt_bytes(limit)}, expected 16GiB "
        f"({OLLAMA_LIMIT_BYTES} bytes)"
    )

    used = stats[name]["used"]
    pct = used / limit * 100
    assert used <= limit, (
        f"{name} using {_fmt_bytes(used)} of {_fmt_bytes(limit)} ({pct:.1f}%)"
    )
    assert pct < OLLAMA_MAX_EXPECTED_PCT, (
        f"{name} at {pct:.1f}% of 16GiB ({_fmt_bytes(used)}); incident baseline "
        f"was {OLLAMA_MAX_EXPECTED_PCT}% of 12GiB — headroom regressed"
    )


def test_neo4j_within_1gb_limit(require_docker):
    stats = _container_stats()
    name = _find_container(stats, "neo4j")
    assert name, f"no neo4j container found; running containers: {sorted(stats)}"

    limit = _memory_limit(name)
    assert limit == NEO4J_LIMIT_BYTES, (
        f"{name} memory limit is {_fmt_bytes(limit)}, expected 1GiB "
        f"({NEO4J_LIMIT_BYTES} bytes)"
    )

    used = stats[name]["used"]
    pct = used / limit * 100
    assert used <= limit, (
        f"{name} using {_fmt_bytes(used)} of {_fmt_bytes(limit)} ({pct:.1f}%)"
    )


def test_metronix_services_within_documented_limits(require_docker):
    stats = _container_stats()
    problems = []

    for service, documented in METRONIX_SERVICE_LIMITS.items():
        name = _find_metronix_container(stats, service)
        if not name:
            problems.append(
                f"{service}: no metronix container found "
                f"(looked for '{'metronix-' + service}')"
            )
            continue

        limit = _memory_limit(name)
        used = stats[name]["used"]
        if limit != documented:
            problems.append(
                f"{service} ({name}): memory limit is {_fmt_bytes(limit)}, "
                f"documented limit is {_fmt_bytes(documented)}"
            )
        if used > limit:
            pct = used / limit * 100 if limit else float("inf")
            problems.append(
                f"{service} ({name}): using {_fmt_bytes(used)} of "
                f"{_fmt_bytes(limit)} ({pct:.1f}%)"
            )

    assert not problems, (
        f"{len(problems)} metronix memory problem(s):\n" + "\n".join(problems)
    )


def test_all_memory_limits_documented_in_compose_files(require_docker):
    stats = _container_stats()
    undocumented = []
    standalone_with_limits = ["neo4j"]

    for name in sorted(stats):
        limit = _memory_limit(name)
        if limit <= 0:
            continue

        workdir, compose_files = _compose_labels(name)
        if not compose_files:
            if name not in standalone_with_limits:
                undocumented.append(
                    f"{name}: has memory limit {_fmt_bytes(limit)} but is not "
                    "managed by docker compose"
                )
            continue

        text = _compose_file_text(workdir, compose_files).lower()
        tokens = _size_tokens(limit)
        if not any(_token_present(text, token) for token in tokens):
            undocumented.append(
                f"{name}: memory limit {_fmt_bytes(limit)} not found in "
                f"{compose_files} (searched tokens: {tokens})"
            )

    assert not undocumented, (
        f"{len(undocumented)} container(s) with undocumented memory limits:\n"
        + "\n".join(undocumented)
    )


def test_no_oom_kills_in_last_24h(require_docker):
    now = int(time.time())
    result = _run_docker(
        "events",
        "--filter",
        "event=oom",
        "--since",
        str(now - 24 * 3600),
        "--until",
        str(now),
        "--format",
        "{{.Actor.Attributes.name}}",
        timeout=30,
    )
    assert result.returncode == 0, f"docker events failed: {result.stderr.strip()}"

    oom_names = sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})
    assert not oom_names, (
        f"{len(oom_names)} container(s) OOM-killed in the last 24h: {oom_names}"
    )
