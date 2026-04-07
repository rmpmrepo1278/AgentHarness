"""Tests for core.security.integrity — SHA-256 manifest verification."""
from __future__ import annotations

import json
import os

from core.security.integrity import generate_checksums, save_checksums, verify_integrity


def _populate(tmp_path):
    """Create a minimal install dir with tracked files."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "deploy.sh").write_text("#!/bin/bash\necho deploy\n")
    (scripts / "setup.py").write_text("print('setup')\n")
    core = tmp_path / "core" / "utils"
    core.mkdir(parents=True)
    (core / "helper.py").write_text("# helper\n")
    return tmp_path


# --- generate_checksums ---

def test_generate_checksums(tmp_path):
    install_dir = _populate(tmp_path)
    checksums = generate_checksums(str(install_dir))
    assert len(checksums) >= 3
    for entry in checksums:
        assert "path" in entry
        assert "sha256" in entry
        assert len(entry["sha256"]) == 64  # hex-encoded SHA-256


# --- verify with no changes ---

def test_verify_no_changes(tmp_path):
    install_dir = _populate(tmp_path)
    manifest = tmp_path / "manifest.json"
    checksums = generate_checksums(str(install_dir))
    save_checksums(checksums, str(manifest))

    result = verify_integrity(str(install_dir), str(manifest))
    assert result["status"] == "ok"
    assert result["modified"] == []
    assert result["missing"] == []


# --- verify detects modification ---

def test_verify_detects_modification(tmp_path):
    install_dir = _populate(tmp_path)
    manifest = tmp_path / "manifest.json"
    checksums = generate_checksums(str(install_dir))
    save_checksums(checksums, str(manifest))

    # Modify a tracked file.
    (install_dir / "scripts" / "deploy.sh").write_text("#!/bin/bash\necho HACKED\n")

    result = verify_integrity(str(install_dir), str(manifest))
    assert result["status"] == "modified"
    assert "scripts/deploy.sh" in result["modified"]


# --- verify detects missing file ---

def test_verify_detects_missing_file(tmp_path):
    install_dir = _populate(tmp_path)
    manifest = tmp_path / "manifest.json"
    checksums = generate_checksums(str(install_dir))
    save_checksums(checksums, str(manifest))

    os.remove(install_dir / "scripts" / "setup.py")

    result = verify_integrity(str(install_dir), str(manifest))
    assert result["status"] == "missing"
    assert "scripts/setup.py" in result["missing"]


# --- new (untracked) files not flagged ---

def test_verify_new_files_not_flagged(tmp_path):
    install_dir = _populate(tmp_path)
    manifest = tmp_path / "manifest.json"
    checksums = generate_checksums(str(install_dir))
    save_checksums(checksums, str(manifest))

    # Add a brand-new file that was not in the manifest.
    (install_dir / "scripts" / "new_thing.sh").write_text("#!/bin/bash\necho new\n")

    result = verify_integrity(str(install_dir), str(manifest))
    assert result["status"] == "ok"
    assert result["modified"] == []
    assert result["missing"] == []
