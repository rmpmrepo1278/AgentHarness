"""Bundle loader — loads and merges YAML bundle files into a unified registry."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.registry.schema import validate_check, validate_harness, validate_tool

logger = logging.getLogger(__name__)

_SECTIONS = ("checks", "tools", "harnesses")
_VALIDATORS = {
    "checks": validate_check,
    "tools": validate_tool,
    "harnesses": validate_harness,
}


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file, returning an empty dict on missing/empty/invalid files."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}


def _merge_section(
    target: Dict[str, Any],
    source: Dict[str, Any],
    section: str,
    bundle_name: str,
    warnings: List[str],
) -> None:
    """Merge *source[section]* into *target[section]*.

    Conflict: same key already in target → warning, later wins.
    Disabled entries (``enabled: false``) stay disabled.
    """
    items = source.get(section)
    if not items or not isinstance(items, dict):
        return
    for name, value in items.items():
        if not isinstance(value, dict):
            continue
        if name in target[section]:
            warnings.append(
                f"{section}/{name}: overridden by bundle '{bundle_name}'"
            )
        target[section][name] = value


def load_registry(
    bundles_dir: Path | str,
    overrides_file: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Load all bundle YAML files and return a unified registry.

    Load order:
        1. bundles_dir/core/   (always first)
        2. remaining bundle dirs in alphabetical order
        3. bundles_dir/community/  (always last before overrides)
        4. overrides_file (user overrides always win)

    Returns
    -------
    dict with keys: checks, tools, harnesses, validation_errors, warnings
    """
    bundles_dir = Path(bundles_dir)
    registry: Dict[str, Any] = {
        "checks": {},
        "tools": {},
        "harnesses": {},
        "validation_errors": [],
        "warnings": [],
    }

    warnings: List[str] = registry["warnings"]

    # Discover bundle directories
    if not bundles_dir.is_dir():
        return registry

    all_dirs = sorted(
        [d for d in bundles_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    # Partition into: core first, community last, rest alphabetical
    core_dirs = [d for d in all_dirs if d.name == "core"]
    community_dirs = [d for d in all_dirs if d.name == "community"]
    other_dirs = [d for d in all_dirs if d.name not in ("core", "community")]

    ordered = core_dirs + other_dirs + community_dirs

    # Load each bundle
    for bundle_dir in ordered:
        bundle_name = bundle_dir.name
        for yaml_file in ("bundle.yaml", "discovered.yaml"):
            data = _load_yaml(bundle_dir / yaml_file)
            if data:
                for section in _SECTIONS:
                    _merge_section(registry, data, section, bundle_name, warnings)

    # Apply user overrides (always win)
    if overrides_file is not None:
        overrides_path = Path(overrides_file)
        data = _load_yaml(overrides_path)
        if data:
            for section in _SECTIONS:
                _merge_section(registry, data, section, "overrides", warnings)

    # Validate all entries
    errors: List[str] = []
    for section in _SECTIONS:
        validator = _VALIDATORS[section]
        for name, entry in registry[section].items():
            errs = validator(name, entry)
            errors.extend(errs)

    registry["validation_errors"] = errors
    return registry
