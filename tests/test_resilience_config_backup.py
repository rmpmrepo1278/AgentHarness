"""Tests for core.resilience.config_backup — snapshot/restore config files."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.resilience.config_backup import (
    snapshot_config,
    restore_config,
    cleanup_old_snapshots,
)


def _populate_config(config_dir: Path) -> dict[str, str]:
    """Create a handful of config files and return {name: content}."""
    config_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "settings.json": '{"mode": "auto"}',
        "agents.yaml": "agents:\n  - name: watchdog\n",
        "sub/nested.conf": "key=value\n",
    }
    for name, content in files.items():
        p = config_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return files


class TestSnapshotConfig:
    def test_snapshot_creates_backup(self, tmp_path: Path):
        """snapshot_config creates a dir with copies of config files."""
        config_dir = tmp_path / "config"
        backup_base = tmp_path / "backups"
        originals = _populate_config(config_dir)

        snap = snapshot_config(config_dir, backup_base)
        snap_path = Path(snap)

        assert snap_path.exists()
        assert snap_path.parent == backup_base
        assert snap_path.name.startswith("config_")

        # Every original file must be present in the snapshot
        for name, content in originals.items():
            assert (snap_path / name).read_text(encoding="utf-8") == content


class TestRestoreConfig:
    def test_restore_from_snapshot(self, tmp_path: Path):
        """Modify config, restore from snapshot, verify original content."""
        config_dir = tmp_path / "config"
        backup_base = tmp_path / "backups"
        originals = _populate_config(config_dir)

        snap = snapshot_config(config_dir, backup_base)

        # Modify a file and add an extra one
        (config_dir / "settings.json").write_text('{"mode": "broken"}', encoding="utf-8")
        (config_dir / "extra.txt").write_text("should vanish", encoding="utf-8")

        restore_config(snap, config_dir)

        # Originals restored
        for name, content in originals.items():
            assert (config_dir / name).read_text(encoding="utf-8") == content

        # Extra file should be gone (restore replaces dir contents)
        assert not (config_dir / "extra.txt").exists()


class TestCleanupOldSnapshots:
    def test_snapshot_limits_kept(self, tmp_path: Path):
        """Create 12 snapshots, cleanup_old_snapshots(keep=5), verify <=5 remain."""
        config_dir = tmp_path / "config"
        backup_base = tmp_path / "backups"
        _populate_config(config_dir)

        # Create 12 snapshots with distinct timestamps
        import time

        for i in range(12):
            snapshot_config(config_dir, backup_base)
            # Ensure distinct timestamps by nudging mtime
            time.sleep(0.02)

        assert len(list(backup_base.iterdir())) == 12

        deleted = cleanup_old_snapshots(backup_base, keep=5)
        remaining = list(backup_base.iterdir())

        assert deleted == 7
        assert len(remaining) <= 5
