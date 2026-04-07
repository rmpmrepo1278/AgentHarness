"""Snapshot config before modifications; restore if something breaks.

Copies the entire config directory to a timestamped backup folder.
If a self-update or config change goes wrong, restore from the snapshot.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def snapshot_config(config_dir: str | Path, backup_base: str | Path) -> str:
    """Copy *config_dir* to a timestamped subdir under *backup_base*.

    Returns the absolute path of the new snapshot directory.
    """
    config_dir = Path(config_dir)
    backup_base = Path(backup_base)
    backup_base.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime(_TIMESTAMP_FMT)
    snapshot_dir = backup_base / f"config_{stamp}"

    # Ensure uniqueness if two snapshots land in the same second
    counter = 1
    candidate = snapshot_dir
    while candidate.exists():
        candidate = backup_base / f"config_{stamp}_{counter}"
        counter += 1
    snapshot_dir = candidate

    shutil.copytree(str(config_dir), str(snapshot_dir))
    logger.info("Config snapshot created: %s", snapshot_dir)
    return str(snapshot_dir)


def restore_config(snapshot_dir: str | Path, config_dir: str | Path) -> None:
    """Replace *config_dir* contents with the snapshot at *snapshot_dir*."""
    snapshot_dir = Path(snapshot_dir)
    config_dir = Path(config_dir)

    if not snapshot_dir.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_dir}")

    # Remove current config and replace with snapshot
    if config_dir.exists():
        shutil.rmtree(str(config_dir))
    shutil.copytree(str(snapshot_dir), str(config_dir))
    logger.info("Config restored from %s", snapshot_dir)


def cleanup_old_snapshots(backup_base: str | Path, keep: int = 10) -> int:
    """Delete oldest snapshot dirs, keeping the *keep* most recent.

    Returns the number of snapshots deleted.
    """
    backup_base = Path(backup_base)
    if not backup_base.exists():
        return 0

    snapshots = sorted(
        [d for d in backup_base.iterdir() if d.is_dir() and d.name.startswith("config_")],
        key=lambda d: d.stat().st_mtime,
    )

    to_delete = snapshots[: max(0, len(snapshots) - keep)]
    for d in to_delete:
        shutil.rmtree(str(d))
        logger.debug("Deleted old snapshot: %s", d)

    deleted = len(to_delete)
    if deleted:
        logger.info("Cleaned up %d old config snapshots, kept %d", deleted, keep)
    return deleted
