"""Snapshot manager — back up files before destructive repair actions."""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import atomic_write_json, safe_read_json

log = logging.getLogger(__name__)


class SnapshotManager:
    """Create, list, rollback, and clean up file snapshots.

    Snapshots are stored as copies in ``{data_dir}/snapshots/`` with a
    JSON index at ``{data_dir}/snapshots.json`` tracking metadata.
    """

    def __init__(self, data_dir: str, max_per_file: int = 5) -> None:
        self.data_dir = Path(data_dir)
        self.snapshots_dir = self.data_dir / "snapshots"
        self.index_path = self.data_dir / "snapshots.json"
        self.max_per_file = max_per_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self, file_path: str, runbook_name: str) -> str:
        """Back up *file_path* before applying *runbook_name*.

        Returns the absolute path to the snapshot copy.
        Raises FileNotFoundError if *file_path* does not exist.
        """
        src = Path(file_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Cannot snapshot missing file: {src}")

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        snap_id = uuid.uuid4().hex[:12]
        ts = time.time()
        dest_name = f"{src.name}.{snap_id}"
        dest = self.snapshots_dir / dest_name
        shutil.copy2(str(src), str(dest))

        entry: dict[str, Any] = {
            "id": snap_id,
            "file_path": str(src),
            "snapshot_path": str(dest),
            "runbook": runbook_name,
            "timestamp": ts,
        }

        index: list[dict[str, Any]] = safe_read_json(self.index_path, default=[])
        index.append(entry)
        atomic_write_json(self.index_path, index)

        log.info("Snapshot %s of %s (runbook=%s)", snap_id, src, runbook_name)
        return str(dest)

    def rollback(self, file_path: str, snapshot_id: str = "latest") -> bool:
        """Restore *file_path* from a snapshot.

        If *snapshot_id* is ``"latest"``, the most recent snapshot for
        *file_path* is used.  Returns True on success, False if no
        matching snapshot exists.
        """
        resolved = str(Path(file_path).resolve())
        index: list[dict[str, Any]] = safe_read_json(self.index_path, default=[])

        candidates = [e for e in index if e["file_path"] == resolved]
        if not candidates:
            log.warning("No snapshots found for %s", resolved)
            return False

        if snapshot_id == "latest":
            entry = max(candidates, key=lambda e: e["timestamp"])
        else:
            matches = [e for e in candidates if e["id"] == snapshot_id]
            if not matches:
                log.warning("Snapshot %s not found for %s", snapshot_id, resolved)
                return False
            entry = matches[0]

        snap_path = Path(entry["snapshot_path"])
        if not snap_path.is_file():
            log.error("Snapshot file missing: %s", snap_path)
            return False

        shutil.copy2(str(snap_path), resolved)
        log.info("Rolled back %s from snapshot %s", resolved, entry["id"])
        return True

    def cleanup(self) -> int:
        """Remove old snapshots beyond *max_per_file* per original file.

        Keeps the most recent snapshots.  Returns the number of removed entries.
        """
        index: list[dict[str, Any]] = safe_read_json(self.index_path, default=[])
        if not index:
            return 0

        # Group by original file path
        by_file: dict[str, list[dict[str, Any]]] = {}
        for entry in index:
            by_file.setdefault(entry["file_path"], []).append(entry)

        keep: list[dict[str, Any]] = []
        removed = 0

        for fp, entries in by_file.items():
            entries.sort(key=lambda e: e["timestamp"], reverse=True)
            keep.extend(entries[: self.max_per_file])
            for old in entries[self.max_per_file :]:
                snap = Path(old["snapshot_path"])
                if snap.is_file():
                    snap.unlink()
                    log.debug("Removed old snapshot %s", snap)
                removed += 1

        if removed:
            atomic_write_json(self.index_path, keep)
            log.info("Cleaned up %d old snapshot(s)", removed)

        return removed

    def list_snapshots(self, file_path: str | None = None) -> list[dict[str, Any]]:
        """Return snapshot entries, optionally filtered to *file_path*."""
        index: list[dict[str, Any]] = safe_read_json(self.index_path, default=[])
        if file_path is not None:
            resolved = str(Path(file_path).resolve())
            index = [e for e in index if e["file_path"] == resolved]
        return index
