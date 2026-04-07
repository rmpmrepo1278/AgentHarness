"""Thread-safe state manager with atomic writes and file locking.

Manages a single state.json file that stores discovered paths, services,
and hardware info. Every write is atomic (tmp + rename) and protected by
fcntl file locking. Reads handle missing/corrupt files gracefully.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
import pathlib
import time
from datetime import datetime, timezone

from core.resilience.watchdog import recover_stale_lock

SCHEMA_VERSION = 1
LOCK_TIMEOUT = 5  # seconds

STATE_FILE = "state.json"
LOCK_FILE = "state.lock"


def _deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge updates into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class StateManager:
    """Thread-safe state manager backed by a JSON file.

    Args:
        data_dir: Directory to store state.json. Falls back to AH_DATA_DIR
                  env var. Creates the directory if it doesn't exist.
    """

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = os.environ.get("AH_DATA_DIR")
        if data_dir is None:
            raise ValueError(
                "data_dir must be provided or AH_DATA_DIR env var must be set"
            )

        self._data_dir = pathlib.Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._data_dir / STATE_FILE
        self._lock_file = self._data_dir / LOCK_FILE

    def read(self) -> dict:
        """Return the current state dict.

        Returns a default state with schema_version if the file doesn't
        exist or is corrupt.
        """
        if not self._state_file.exists():
            return {"schema_version": SCHEMA_VERSION}

        try:
            text = self._state_file.read_text(encoding="utf-8")
            state = json.loads(text)
            if not isinstance(state, dict):
                return {"schema_version": SCHEMA_VERSION}
            return state
        except (json.JSONDecodeError, OSError):
            return {"schema_version": SCHEMA_VERSION}

    def write(self, updates: dict) -> dict:
        """Atomically merge updates into state and persist to disk.

        Uses fcntl file locking for thread/process safety and writes to
        a temp file before renaming for crash safety. Adds a last_updated
        timestamp on every write.

        Args:
            updates: Dict to deep-merge into the current state.

        Returns:
            The new merged state dict.
        """
        recover_stale_lock(str(self._lock_file))
        lock_fd = None
        try:
            # Acquire file lock
            lock_fd = open(self._lock_file, "w")
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (IOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Could not acquire state lock within {LOCK_TIMEOUT}s"
                        )
                    time.sleep(0.05)

            # Read current state under lock
            current = self.read()

            # Deep merge
            merged = _deep_merge(current, updates)
            merged["schema_version"] = SCHEMA_VERSION
            merged["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Atomic write: tmp file + rename
            tmp_file = self._state_file.with_suffix(".json.tmp")
            tmp_file.write_text(
                json.dumps(merged, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_file.rename(self._state_file)

            return merged
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    lock_fd.close()
                except OSError:
                    pass

    def ensure_fresh(self) -> list:
        """Validate that cached paths still exist on disk.

        Returns:
            List of path keys whose targets no longer exist.
        """
        state = self.read()
        paths = state.get("paths", {})
        stale = []
        for key, path_str in paths.items():
            if not os.path.exists(path_str):
                stale.append(key)
        return stale

    def resolve(self, key: str, default: str = None) -> str:
        """Get a single path value from state.

        Args:
            key: The path key to look up under state["paths"].
            default: Value to return if key is not found.

        Returns:
            The path string, or default if not found.
        """
        state = self.read()
        return state.get("paths", {}).get(key, default)
