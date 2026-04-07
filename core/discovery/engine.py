"""Discovery engine coordinator — runs all discovery modules and writes state.

Central entry point that orchestrates path, hardware, service, and agent
discovery, then persists results via StateManager.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.discovery.state import StateManager
from core.discovery.paths import discover_paths
from core.discovery.hardware import discover_hardware
from core.discovery.services import discover_services
from core.discovery.agents import discover_agents
from core.resilience.selftest import run_selftest
from core.resilience.circuit_breaker import CircuitBreaker
from core.security.integrity import generate_checksums, save_checksums


def run_discovery(
    hint_dir: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run full discovery and write results to state.json."""
    paths = discover_paths(hint_dir=hint_dir, overrides=overrides)
    hardware = discover_hardware()
    services = discover_services()
    agents = discover_agents()

    data_dir = paths.get("data_dir", paths["install_dir"])
    sm = StateManager(data_dir=data_dir)
    sm.write({
        "paths": paths,
        "hardware": hardware,
        "services": services,
        "agents": agents,
    })

    # Run self-test and persist results
    selftest = run_selftest(data_dir=data_dir)
    sm.write({"selftest": selftest})

    # Reset circuit breakers (services may have changed)
    cb = CircuitBreaker(data_dir=data_dir)
    cb.reset_all()

    # Generate integrity manifest
    manifest_path = str(Path(data_dir) / "integrity_manifest.json")
    checksums = generate_checksums(paths["install_dir"])
    save_checksums(checksums, manifest_path)

    return sm.read()
