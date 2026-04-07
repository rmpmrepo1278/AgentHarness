"""Tests for core.resilience.atomic_json — crash-safe JSON read/write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.resilience.atomic_json import (
    safe_read_json,
    atomic_write_json,
    atomic_append_json,
)


class TestSafeReadJson:
    def test_write_and_read(self, tmp_path: Path):
        """Write a dict then read it back identically."""
        target = tmp_path / "data.json"
        data = {"key": "value", "count": 42}
        atomic_write_json(target, data)
        assert safe_read_json(target) == data

    def test_read_nonexistent_returns_default(self, tmp_path: Path):
        """Missing file returns the caller-supplied default."""
        missing = tmp_path / "nope.json"
        assert safe_read_json(missing) is None
        assert safe_read_json(missing, default=[]) == []

    def test_read_corrupt_file_returns_default(self, tmp_path: Path):
        """Broken JSON returns the default value."""
        bad = tmp_path / "corrupt.json"
        bad.write_text("{not valid json!!", encoding="utf-8")
        assert safe_read_json(bad, default={"fallback": True}) == {"fallback": True}

    def test_corrupt_file_backed_up(self, tmp_path: Path):
        """Corrupt file gets renamed to .corrupt as a backup."""
        bad = tmp_path / "state.json"
        bad.write_text("<<<garbage>>>", encoding="utf-8")
        safe_read_json(bad, default={})
        backup = tmp_path / "state.json.corrupt"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "<<<garbage>>>"


class TestAtomicWriteJson:
    def test_atomic_write_survives_tmp(self, tmp_path: Path):
        """A leftover .tmp file doesn't corrupt the valid file."""
        target = tmp_path / "queue.json"
        # Write valid data first
        atomic_write_json(target, {"ok": True})
        # Simulate a leftover .tmp from a crashed previous write
        tmp_file = tmp_path / "queue.json.tmp"
        tmp_file.write_text("partial garbage", encoding="utf-8")
        # Reading should still return the valid data
        assert safe_read_json(target) == {"ok": True}

    def test_creates_parent_dirs(self, tmp_path: Path):
        """Parent directories are created automatically."""
        deep = tmp_path / "a" / "b" / "c" / "data.json"
        atomic_write_json(deep, [1, 2, 3])
        assert safe_read_json(deep) == [1, 2, 3]


class TestAtomicAppendJson:
    def test_append_to_list(self, tmp_path: Path):
        """atomic_append_json appends items and preserves order."""
        target = tmp_path / "events.json"
        atomic_append_json(target, "first")
        atomic_append_json(target, "second")
        atomic_append_json(target, "third")
        result = safe_read_json(target)
        assert result == ["first", "second", "third"]

    def test_append_creates_list_if_missing(self, tmp_path: Path):
        """Appending to a nonexistent file creates a new list."""
        target = tmp_path / "new_list.json"
        atomic_append_json(target, {"event": "boot"})
        assert safe_read_json(target) == [{"event": "boot"}]
