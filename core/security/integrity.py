"""File integrity verification — SHA-256 manifest for shipped scripts and bundles."""
from __future__ import annotations

import hashlib
import json
import pathlib

# Glob patterns for files we track in the manifest.
TRACKED_PATTERNS: list[str] = [
    "scripts/*.sh",
    "scripts/*.py",
    "bundles/*/bundle.yaml",
    "core/**/*.py",
    "install.sh",
    "cli.py",
]


def _sha256(filepath: str) -> str:
    """Return the hex-encoded SHA-256 digest of *filepath*."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_checksums(install_dir: str) -> list[dict]:
    """Glob tracked patterns under *install_dir* and compute SHA-256 for each.

    Returns a list of ``{"path": <relative>, "sha256": <hex>}`` dicts.
    """
    root = pathlib.Path(install_dir)
    seen: set[str] = set()
    results: list[dict] = []
    for pattern in TRACKED_PATTERNS:
        for match in sorted(root.glob(pattern)):
            if not match.is_file():
                continue
            rel = str(match.relative_to(root))
            if rel in seen:
                continue
            seen.add(rel)
            results.append({"path": rel, "sha256": _sha256(str(match))})
    return results


def save_checksums(checksums: list[dict], manifest_path: str) -> None:
    """Write *checksums* list to *manifest_path* as JSON."""
    with open(manifest_path, "w") as f:
        json.dump(checksums, f, indent=2)


def verify_integrity(install_dir: str, manifest_path: str) -> dict:
    """Compare current files against the saved manifest.

    Returns::

        {
            "status": "ok" | "modified" | "missing" | "no_manifest",
            "modified": [<relative paths>],
            "missing":  [<relative paths>],
            "checked":  <int>,
        }
    """
    mp = pathlib.Path(manifest_path)
    if not mp.exists():
        return {"status": "no_manifest", "modified": [], "missing": [], "checked": 0}

    with open(mp) as f:
        manifest: list[dict] = json.load(f)

    root = pathlib.Path(install_dir)
    modified: list[str] = []
    missing: list[str] = []

    for entry in manifest:
        fpath = root / entry["path"]
        if not fpath.exists():
            missing.append(entry["path"])
            continue
        current_hash = _sha256(str(fpath))
        if current_hash != entry["sha256"]:
            modified.append(entry["path"])

    if missing:
        status = "missing"
    elif modified:
        status = "modified"
    else:
        status = "ok"

    return {
        "status": status,
        "modified": modified,
        "missing": missing,
        "checked": len(manifest),
    }
