"""Path discovery — resolve install directory and derive all standard paths.

Resolution order:
  1. AGENTHARNESS_HOME env var
  2. hint_dir argument
  3. Convention probing (~agentharness, /opt/agentharness, etc.)
  4. Walk up from this file's location

Raises RuntimeError if the install directory cannot be found.
"""

from __future__ import annotations

import os
import pathlib

# Convention locations to probe, in priority order.
_CONVENTION_LOCATIONS = [
    "~/agentharness",
    "/opt/agentharness",
    "~/.agentharness",
    "~/.local/share/agentharness",
]

# Data subdirectories that must exist at runtime.
_DATA_SUBDIRS = ("reports", "logs", "proposals", "briefings", "custom")


def _find_by_walking_up() -> str | None:
    """Walk up from this file's directory looking for a plausible install root.

    A directory is accepted if it contains a 'core' subdirectory (indicating
    it is the AgentHarness repo root).
    """
    current = pathlib.Path(__file__).resolve().parent
    for _ in range(10):  # safety cap
        if (current / "core").is_dir() and current != pathlib.Path("/"):
            return str(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def discover_paths(
    hint_dir: str | None = None,
    overrides: dict | None = None,
) -> dict[str, str]:
    """Discover the install directory and derive all standard paths.

    Args:
        hint_dir: Optional path hint (e.g. the directory the calling script
                  lives in). Used if the env var is not set.
        overrides: Optional dict of path keys to override after discovery.
                   Override values win unconditionally.

    Returns:
        Dict mapping path names to absolute path strings.

    Raises:
        RuntimeError: If the install directory cannot be resolved.
    """
    install_dir: str | None = None

    # 1. Env var
    env_home = os.environ.get("AGENTHARNESS_HOME")
    if env_home and os.path.isdir(env_home):
        install_dir = env_home

    # 2. hint_dir
    if install_dir is None and hint_dir and os.path.isdir(hint_dir):
        install_dir = hint_dir

    # 3. Convention probing
    if install_dir is None:
        for loc in _CONVENTION_LOCATIONS:
            expanded = os.path.expanduser(loc)
            if os.path.isdir(expanded):
                install_dir = expanded
                break

    # 4. Walk up from __file__
    if install_dir is None:
        install_dir = _find_by_walking_up()

    if install_dir is None:
        raise RuntimeError(
            "Cannot find AgentHarness install directory. "
            "Set AGENTHARNESS_HOME or pass hint_dir."
        )

    install = pathlib.Path(install_dir).resolve()

    # Determine data_dir: AH_DATA_DIR env var → install_dir/data
    data_dir_env = os.environ.get("AH_DATA_DIR")
    data_dir = pathlib.Path(data_dir_env) if data_dir_env else install / "data"

    # Determine model_dir: /opt/models if it exists, else install_dir/models
    opt_models = pathlib.Path("/opt/models")
    model_dir = opt_models if opt_models.is_dir() else install / "models"

    # Build the paths dict
    paths: dict[str, str] = {
        "install_dir": str(install),
        "scripts_dir": str(install / "scripts"),
        "config_dir": str(install / "config"),
        "bundles_dir": str(install / "bundles"),
        "core_dir": str(install / "core"),
        "data_dir": str(data_dir),
        "reports_dir": str(data_dir / "reports"),
        "logs_dir": str(data_dir / "logs"),
        "proposals_dir": str(data_dir / "proposals"),
        "briefings_dir": str(data_dir / "briefings"),
        "custom_dir": str(data_dir / "custom"),
        "model_dir": str(model_dir),
    }

    # Apply overrides
    if overrides:
        paths.update(overrides)

    # Create data directories that should exist
    for key in ("data_dir", "reports_dir", "logs_dir", "proposals_dir", "briefings_dir", "custom_dir"):
        pathlib.Path(paths[key]).mkdir(parents=True, exist_ok=True)

    return paths
