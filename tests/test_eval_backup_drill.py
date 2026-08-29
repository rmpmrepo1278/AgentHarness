"""Backup & maintenance drill evals.

Verifies the homelab's backup pipeline end-to-end:
  - postgres_backup.sh and docker_build_prune.sh exist and are executable
  - both scripts carry the expected structure (safe shell options, tools)
  - script constants match the expected deployment values
  - Kopia repository is reachable and holds a snapshot younger than 24h
  - the on-disk dump directory exists and received fresh dumps

All shell interaction goes through subprocess.run. Tests auto-skip when
not running on the homelab (/home/rohit missing); privileged Kopia checks
additionally require root.
"""

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

HOMELAB_HOME = Path("/home/rohit")
POSTGRES_BACKUP = HOMELAB_HOME / ".hermes/scripts/postgres_backup.sh"
DOCKER_BUILD_PRUNE = HOMELAB_HOME / ".hermes/scripts/docker_build_prune.sh"
BACKUP_DIR = Path("/mnt/usb/backups/db-dumps")

EXPECTED_BACKUP_DIR = "/mnt/usb/backups/db-dumps"
EXPECTED_CONTAINER = "metronix-full-postgres"

SNAPSHOT_MAX_AGE_HOURS = 24
DUMP_MAX_AGE_HOURS = 24


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


is_homelab = pytest.mark.skipif(
    not HOMELAB_HOME.exists(),
    reason="not on homelab (/home/rohit missing)",
)
requires_root = pytest.mark.skipif(
    not _is_root(),
    reason="requires root privileges",
)


def run_cmd(*cmd: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a command via subprocess.run and return the completed process."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def parse_kopia_time(raw: str) -> datetime:
    """Parse a Kopia ISO-8601 timestamp (may carry nanosecond precision)."""
    normalized = raw.replace("Z", "+00:00")
    trimmed = re.match(r"(.*\.\d{6})\d*([+-]\d{2}:\d{2})?$", normalized)
    if trimmed:
        normalized = trimmed.group(1) + (trimmed.group(2) or "")
    return datetime.fromisoformat(normalized)


# --- Script presence -------------------------------------------------------


@is_homelab
def test_postgres_backup_script_exists_and_executable():
    """postgres_backup.sh exists and carries the executable bit."""
    assert POSTGRES_BACKUP.is_file(), f"{POSTGRES_BACKUP} does not exist"
    assert os.access(POSTGRES_BACKUP, os.X_OK), f"{POSTGRES_BACKUP} is not executable"


@is_homelab
def test_docker_build_prune_script_exists_and_executable():
    """docker_build_prune.sh exists and carries the executable bit."""
    assert DOCKER_BUILD_PRUNE.is_file(), f"{DOCKER_BUILD_PRUNE} does not exist"
    assert os.access(DOCKER_BUILD_PRUNE, os.X_OK), f"{DOCKER_BUILD_PRUNE} is not executable"


# --- Script structure ------------------------------------------------------


@is_homelab
def test_postgres_backup_script_structure():
    """postgres_backup.sh follows the expected backup recipe."""
    content = POSTGRES_BACKUP.read_text()

    assert "set -euo pipefail" in content, "script must fail fast (set -euo pipefail)"
    assert re.search(r"^BACKUP_DIR=", content, re.MULTILINE), "must define BACKUP_DIR"
    assert "docker exec" in content, "dump must run inside the container (docker exec)"
    assert "pg_dump" in content, "must use pg_dump for a consistent SQL dump"
    assert "gzip" in content, "dump must be gzip-compressed"
    assert (
        "-mtime" in content or "-delete" in content or "RETENTION" in content.upper()
    ), "must implement retention pruning of old dumps"


@is_homelab
def test_docker_build_prune_script_structure():
    """docker_build_prune.sh prunes build cache and dangling images."""
    content = DOCKER_BUILD_PRUNE.read_text()

    assert "set -euo pipefail" in content, "script must fail fast (set -euo pipefail)"
    assert "docker builder prune" in content, "must prune the builder cache"
    assert "docker image prune" in content, "must prune dangling images"


# --- Script constants ------------------------------------------------------


def extract_constant(content: str, name: str) -> str:
    match = re.search(rf'^{name}="?([^"\n]+)"?', content, re.MULTILINE)
    assert match, f"{name}= constant not found in script"
    return match.group(1).strip()


@is_homelab
def test_script_constants_match_expected_values():
    """BACKUP_DIR and CONTAINER constants match the deployed values."""
    content = POSTGRES_BACKUP.read_text()

    backup_dir = extract_constant(content, "BACKUP_DIR").rstrip("/")
    container = extract_constant(content, "CONTAINER")

    assert backup_dir == EXPECTED_BACKUP_DIR.rstrip("/"), (
        f"BACKUP_DIR is {backup_dir!r}, expected {EXPECTED_BACKUP_DIR!r}"
    )
    assert container == EXPECTED_CONTAINER, (
        f"CONTAINER is {container!r}, expected {EXPECTED_CONTAINER!r}"
    )


# --- Kopia repository health ----------------------------------------------


@requires_root
@is_homelab
def test_kopia_repository_accessible():
    """Kopia repository status succeeds."""
    result = run_cmd("kopia", "repository", "status")
    assert result.returncode == 0, (
        f"kopia repository status failed (rc={result.returncode}): "
        f"{result.stderr.strip()}"
    )


@requires_root
@is_homelab
def test_kopia_snapshot_within_last_24_hours():
    """At least one Kopia snapshot was taken within the last 24 hours."""
    result = run_cmd("kopia", "snapshot", "list", "--json")
    assert result.returncode == 0, (
        f"kopia snapshot list failed (rc={result.returncode}): {result.stderr.strip()}"
    )

    snapshots = json.loads(result.stdout)
    assert snapshots, "no Kopia snapshots found at all"

    newest = max(parse_kopia_time(s["startTime"]) for s in snapshots)
    age_hours = (datetime.now(UTC) - newest).total_seconds() / 3600
    assert age_hours <= SNAPSHOT_MAX_AGE_HOURS, (
        f"newest Kopia snapshot is {age_hours:.1f}h old "
        f"(limit {SNAPSHOT_MAX_AGE_HOURS}h)"
    )


# --- On-disk dumps ---------------------------------------------------------


@is_homelab
def test_backup_dir_has_recent_dumps():
    """/mnt/usb/backups/db-dumps exists and holds a dump younger than 24h."""
    assert BACKUP_DIR.is_dir(), f"{BACKUP_DIR} does not exist"

    dumps = [p for p in BACKUP_DIR.iterdir() if p.is_file()]
    assert dumps, f"{BACKUP_DIR} contains no dump files"

    now = time.time()
    newest_age_hours = min((now - p.stat().st_mtime) / 3600 for p in dumps)
    assert newest_age_hours <= DUMP_MAX_AGE_HOURS, (
        f"newest dump in {BACKUP_DIR} is {newest_age_hours:.1f}h old "
        f"(limit {DUMP_MAX_AGE_HOURS}h)"
    )
