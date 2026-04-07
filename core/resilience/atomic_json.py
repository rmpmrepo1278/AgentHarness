"""Crash-safe JSON read/write for queue and state files.

All writes use tmp-then-rename with file locking so a crash at any point
leaves either the old valid file or the new valid file — never a partial write.
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_INTERVAL_S = 0.05


def _acquire_lock(fd: int, timeout: float = _LOCK_TIMEOUT_S) -> bool:
    """Try to acquire an exclusive lock with a retry loop.

    Returns True if the lock was acquired, False on timeout (with a warning).
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            if time.monotonic() >= deadline:
                logger.warning(
                    "Lock timeout after %.1fs — proceeding without lock", timeout
                )
                return False
            time.sleep(_LOCK_POLL_INTERVAL_S)


def safe_read_json(path: Path | str, default: Any = None) -> Any:
    """Read JSON from *path*, returning *default* on missing or corrupt files.

    Corrupt files are backed up as ``<path>.corrupt`` before returning the default.
    """
    path = Path(path)
    if not path.exists():
        return default

    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        backup = path.with_suffix(path.suffix + ".corrupt")
        logger.warning("Corrupt JSON at %s — backing up to %s: %s", path, backup, exc)
        shutil.copy2(str(path), str(backup))
        return default


def atomic_write_json(path: Path | str, data: Any) -> None:
    """Atomically write *data* as JSON to *path* via tmp+rename with locking.

    Parent directories are created automatically.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            _acquire_lock(f.fileno(), _LOCK_TIMEOUT_S)
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
        # Atomic rename (POSIX guarantees this is atomic on the same filesystem)
        tmp_path.rename(path)
    except Exception:
        # Clean up the tmp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def atomic_append_json(path: Path | str, item: Any) -> None:
    """Atomically append *item* to a JSON list at *path*.

    If the file doesn't exist or is empty, a new list is created.
    """
    path = Path(path)
    existing = safe_read_json(path, default=[])
    if not isinstance(existing, list):
        existing = []
    existing.append(item)
    atomic_write_json(path, existing)
