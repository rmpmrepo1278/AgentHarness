"""Tests for core.doctor.snapshot — SnapshotManager."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.doctor.snapshot import SnapshotManager


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture()
def mgr(data_dir: Path) -> SnapshotManager:
    data_dir.mkdir(parents=True, exist_ok=True)
    return SnapshotManager(data_dir=str(data_dir), max_per_file=3)


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    f = tmp_path / "config.json"
    f.write_text('{"key": "original"}')
    return f


# ------------------------------------------------------------------
# snapshot
# ------------------------------------------------------------------


def test_snapshot_creates_backup_and_updates_index(
    mgr: SnapshotManager, sample_file: Path, data_dir: Path
) -> None:
    snap_path = mgr.snapshot(str(sample_file), runbook_name="fix_config")

    # Backup file exists with the same content
    assert Path(snap_path).is_file()
    assert Path(snap_path).read_text() == sample_file.read_text()

    # Index has one entry
    index = json.loads((data_dir / "snapshots.json").read_text())
    assert len(index) == 1
    entry = index[0]
    assert entry["file_path"] == str(sample_file.resolve())
    assert entry["runbook"] == "fix_config"
    assert entry["snapshot_path"] == snap_path


def test_snapshot_missing_file_raises(mgr: SnapshotManager) -> None:
    with pytest.raises(FileNotFoundError):
        mgr.snapshot("/nonexistent/path.txt", runbook_name="nope")


# ------------------------------------------------------------------
# rollback
# ------------------------------------------------------------------


def test_rollback_restores_original_content(
    mgr: SnapshotManager, sample_file: Path
) -> None:
    mgr.snapshot(str(sample_file), runbook_name="fix_config")

    # Mutate the original
    sample_file.write_text('{"key": "mutated"}')
    assert sample_file.read_text() == '{"key": "mutated"}'

    result = mgr.rollback(str(sample_file))
    assert result is True
    assert sample_file.read_text() == '{"key": "original"}'


def test_rollback_specific_snapshot(
    mgr: SnapshotManager, sample_file: Path
) -> None:
    mgr.snapshot(str(sample_file), runbook_name="first")
    first_id = mgr.list_snapshots(str(sample_file))[0]["id"]

    sample_file.write_text("v2")
    mgr.snapshot(str(sample_file), runbook_name="second")

    sample_file.write_text("v3")

    result = mgr.rollback(str(sample_file), snapshot_id=first_id)
    assert result is True
    assert sample_file.read_text() == '{"key": "original"}'


def test_rollback_nonexistent_file_returns_false(mgr: SnapshotManager) -> None:
    result = mgr.rollback("/does/not/exist.json")
    assert result is False


def test_rollback_nonexistent_snapshot_id_returns_false(
    mgr: SnapshotManager, sample_file: Path
) -> None:
    mgr.snapshot(str(sample_file), runbook_name="fix")
    result = mgr.rollback(str(sample_file), snapshot_id="bogus_id")
    assert result is False


# ------------------------------------------------------------------
# cleanup
# ------------------------------------------------------------------


def test_cleanup_keeps_only_max_per_file(
    mgr: SnapshotManager, sample_file: Path, data_dir: Path
) -> None:
    # Create 5 snapshots (max_per_file=3)
    for i in range(5):
        sample_file.write_text(f"version_{i}")
        mgr.snapshot(str(sample_file), runbook_name=f"run_{i}")

    assert len(mgr.list_snapshots(str(sample_file))) == 5

    removed = mgr.cleanup()
    assert removed == 2

    remaining = mgr.list_snapshots(str(sample_file))
    assert len(remaining) == 3

    # The 3 newest should survive (version_2, version_3, version_4)
    remaining.sort(key=lambda e: e["timestamp"])
    for entry in remaining:
        assert Path(entry["snapshot_path"]).is_file()


def test_cleanup_no_snapshots_returns_zero(mgr: SnapshotManager) -> None:
    assert mgr.cleanup() == 0


# ------------------------------------------------------------------
# list_snapshots
# ------------------------------------------------------------------


def test_list_snapshots_all(
    mgr: SnapshotManager, sample_file: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other.txt"
    other.write_text("other")

    mgr.snapshot(str(sample_file), runbook_name="a")
    mgr.snapshot(str(other), runbook_name="b")

    assert len(mgr.list_snapshots()) == 2
    assert len(mgr.list_snapshots(str(sample_file))) == 1
    assert len(mgr.list_snapshots(str(other))) == 1


def test_list_snapshots_empty(mgr: SnapshotManager) -> None:
    assert mgr.list_snapshots() == []
    assert mgr.list_snapshots("/any/file") == []
