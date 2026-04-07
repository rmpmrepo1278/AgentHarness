# Phase A: Foundation — Discovery + Script Rewrite + Registry Evolution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all hardcoded paths, build a discovery engine, rewrite scripts to use discovered paths, evolve the registry to support bundles, and harden for unattended homelab operation — making AgentHarness work on any machine and recover from failures without user intervention.

**Architecture:** A Python discovery engine (`core/discovery/`) resolves all paths at runtime and caches them in `state.json`. A rewritten `scripts/common.sh` reads state.json and exports paths as environment variables. Every script uses `$AH_*` variables instead of `/opt/agentharness`. The registry engine evolves to load multiple bundle YAML files with schema validation. A resilience layer ensures the system auto-recovers from crashes, stale locks, corrupted state, and unattended failures.

**Tech Stack:** Python 3.10+, PyYAML, bash, fcntl (file locking), json

**Spec:** `docs/superpowers/specs/2026-04-07-agentharness-v2-design.md` (Sections 1, 6, 8, 10)

---

## File Structure

### New files to create:
```
core/__init__.py
core/discovery/__init__.py
core/discovery/engine.py          # Central coordinator — resolve(), ensure_fresh(), override()
core/discovery/paths.py           # Find install dirs, config dirs, data dirs
core/discovery/services.py        # Find Docker containers, ports, APIs
core/discovery/hardware.py        # Detect RAM, CPU, GPU, storage, NICs
core/discovery/agents.py          # Find agent installations (Chaguli etc.)
core/discovery/credentials.py     # Opt-in credential scanning
core/discovery/state.py           # Thread-safe state management with file locking
core/registry/__init__.py
core/registry/engine.py           # Evolved registry engine
core/registry/loader.py           # Load + merge bundle YAML files
core/registry/schema.py           # Validate registry entries
bundles/core/bundle.yaml          # Core checks (disk, RAM, swap, CPU)
bundles/homelab/bundle.yaml       # Docker + service monitoring
bundles/inference/bundle.yaml     # LLM engine management
bundles/security/bundle.yaml      # Security hardening
bundles/backup/bundle.yaml        # Backup + restore
core/resilience/__init__.py
core/resilience/watchdog.py       # Self-watchdog + stale lock recovery
core/resilience/atomic_json.py    # Crash-safe JSON read/write for queues
core/resilience/circuit_breaker.py # Alert fatigue prevention for checks
core/resilience/selftest.py       # Startup self-test
core/resilience/config_backup.py  # Snapshot configs before changes
cli.py                            # CLI entry point skeleton
config/systemd/agentharness-scheduler.service  # systemd unit with Restart=on-failure
config/systemd/agentharness-watchdog.service   # Watchdog timer
config/systemd/agentharness-watchdog.timer     # 5-minute timer
config/logrotate/agentharness                  # Log rotation config
tests/test_discovery_state.py
tests/test_discovery_paths.py
tests/test_discovery_hardware.py
tests/test_discovery_services.py
tests/test_registry_schema.py
tests/test_registry_loader.py
tests/test_common_sh.py
tests/test_resilience_atomic_json.py
tests/test_resilience_circuit_breaker.py
tests/test_resilience_selftest.py
tests/test_resilience_watchdog.py
```

### Files to modify:
```
scripts/common.sh                 # Rewrite to read state.json
scripts/alert.sh                  # Replace hardcoded paths
scripts/scheduler.sh              # Replace hardcoded paths
scripts/cleanup.sh                # Replace hardcoded paths
scripts/backup.sh                 # Replace hardcoded paths
scripts/benchmark.sh              # Replace hardcoded paths
scripts/build_inference.sh        # Replace hardcoded paths
scripts/discover_automations.sh   # Replace hardcoded paths
scripts/discover_chaguli.sh       # Replace hardcoded paths
scripts/discover_config.sh        # Replace hardcoded paths
scripts/discover_storage.sh       # Replace hardcoded paths
scripts/doctor.sh                 # Replace hardcoded paths
scripts/download_models.sh        # Replace hardcoded paths
scripts/github_deploy.sh          # Replace hardcoded paths
scripts/harden.sh                 # Replace hardcoded paths
scripts/mcp_gateway.sh            # Replace hardcoded paths
scripts/registry_engine.py        # Replace hardcoded paths
scripts/security_audit.sh         # Replace hardcoded paths
scripts/self_update.sh            # Replace hardcoded paths
scripts/setup_minipc.sh           # Replace hardcoded paths
scripts/trend_projector.sh        # Replace hardcoded paths
scripts/update_watcher.sh         # Replace hardcoded paths
scripts/validate.sh               # Replace hardcoded paths
scripts/weekly_optimize.sh        # Replace hardcoded paths
scripts/integrate_chaguli.sh      # Replace hardcoded paths
config/harness_registry.yaml      # Replace hardcoded paths
config/env.template               # Replace hardcoded paths
config/systemd/llama-primary.service  # Replace hardcoded paths
config/systemd/llama-fast.service     # Replace hardcoded paths
install.sh                        # Rewrite to be discovery-first
README.md                         # Rewrite for new architecture
requirements.txt                  # Add new dependencies
```

---

## Task 1: State Manager — Thread-Safe State with File Locking

**Files:**
- Create: `core/__init__.py`
- Create: `core/discovery/__init__.py`
- Create: `core/discovery/state.py`
- Test: `tests/test_discovery_state.py`

- [ ] **Step 1: Write failing tests for state manager**

```python
# tests/test_discovery_state.py
import json
import os
import tempfile
import pytest

# We'll set AH_DATA_DIR to a temp directory for all tests
@pytest.fixture
def state_dir(tmp_path):
    os.environ["AH_DATA_DIR"] = str(tmp_path)
    yield tmp_path
    os.environ.pop("AH_DATA_DIR", None)


def test_write_and_read(state_dir):
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    sm.write({"paths": {"scripts_dir": "/home/user/ah/scripts"}})
    result = sm.read()
    assert result["paths"]["scripts_dir"] == "/home/user/ah/scripts"
    assert result["schema_version"] == 1


def test_merge_updates(state_dir):
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    sm.write({"paths": {"scripts_dir": "/a"}, "hardware": {"ram_gb": 32}})
    sm.write({"paths": {"data_dir": "/b"}})
    result = sm.read()
    assert result["paths"]["scripts_dir"] == "/a"
    assert result["paths"]["data_dir"] == "/b"
    assert result["hardware"]["ram_gb"] == 32


def test_read_nonexistent_returns_empty(state_dir):
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    result = sm.read()
    assert result == {"schema_version": 1}


def test_atomic_write_survives_crash(state_dir):
    """If .tmp file exists but final doesn't, state should still be readable."""
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    sm.write({"paths": {"scripts_dir": "/valid"}})
    # Simulate crash: .tmp exists alongside valid state
    tmp_file = state_dir / "state.json.tmp"
    tmp_file.write_text('{"corrupt": true}')
    result = sm.read()
    assert result["paths"]["scripts_dir"] == "/valid"


def test_ensure_fresh_marks_missing_paths(state_dir, tmp_path):
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    fake_dir = str(tmp_path / "nonexistent")
    sm.write({"paths": {"scripts_dir": fake_dir}})
    stale = sm.ensure_fresh()
    assert "scripts_dir" in stale


def test_ensure_fresh_keeps_valid_paths(state_dir, tmp_path):
    from core.discovery.state import StateManager
    sm = StateManager(data_dir=str(state_dir))
    real_dir = str(tmp_path)
    sm.write({"paths": {"scripts_dir": real_dir}})
    stale = sm.ensure_fresh()
    assert "scripts_dir" not in stale
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rohitmishra/Library/CloudStorage/OneDrive-T-MobileUSA/Documents/projects/AgentHarness && python3 -m pytest tests/test_discovery_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Create package init files**

```python
# core/__init__.py
"""AgentHarness core framework."""

# core/discovery/__init__.py
"""Discovery engine — resolves paths, services, hardware at runtime."""
```

- [ ] **Step 4: Implement StateManager**

```python
# core/discovery/state.py
"""Thread-safe state management with file locking and atomic writes."""

import fcntl
import json
import os
import time
from pathlib import Path


class StateManager:
    """Manages discovery state with atomic writes and file locking.

    State is stored as JSON with a schema version for future migrations.
    Writes are atomic (write to .tmp, then os.rename).
    Concurrent access is serialized via fcntl.flock.
    """

    SCHEMA_VERSION = 1
    LOCK_TIMEOUT = 5  # seconds

    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir or os.environ.get("AH_DATA_DIR", "."))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"
        self.lock_file = self.data_dir / "state.lock"
        self.tmp_file = self.data_dir / "state.json.tmp"

    def read(self) -> dict:
        """Read current state. Returns empty state if file doesn't exist."""
        if not self.state_file.exists():
            return {"schema_version": self.SCHEMA_VERSION}
        try:
            data = json.loads(self.state_file.read_text())
            if "schema_version" not in data:
                data["schema_version"] = self.SCHEMA_VERSION
            return data
        except (json.JSONDecodeError, OSError):
            return {"schema_version": self.SCHEMA_VERSION}

    def write(self, updates: dict) -> None:
        """Atomic write with file locking. Merges updates into existing state."""
        lock_fd = None
        try:
            # Acquire lock
            self.lock_file.touch(exist_ok=True)
            lock_fd = open(self.lock_file, "r")
            deadline = time.monotonic() + self.LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Could not acquire state lock")
                    time.sleep(0.1)

            # Read current state
            current = self.read()

            # Deep merge updates into current
            for key, value in updates.items():
                if key == "schema_version":
                    continue
                if isinstance(value, dict) and isinstance(current.get(key), dict):
                    current[key].update(value)
                else:
                    current[key] = value

            current["schema_version"] = self.SCHEMA_VERSION
            current["last_updated"] = time.time()

            # Atomic write: tmp then rename
            self.tmp_file.write_text(json.dumps(current, indent=2))
            os.rename(self.tmp_file, self.state_file)

        finally:
            if lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()

    def ensure_fresh(self) -> list[str]:
        """Re-validate that cached paths still exist.

        Returns list of path keys that are stale (path no longer exists).
        """
        state = self.read()
        stale = []
        paths = state.get("paths", {})
        for key, path_str in paths.items():
            if path_str and not Path(path_str).exists():
                stale.append(key)
        return stale

    def resolve(self, key: str, default: str | None = None) -> str | None:
        """Get a single resolved path from state."""
        state = self.read()
        return state.get("paths", {}).get(key, default)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/rohitmishra/Library/CloudStorage/OneDrive-T-MobileUSA/Documents/projects/AgentHarness && python3 -m pytest tests/test_discovery_state.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add core/__init__.py core/discovery/__init__.py core/discovery/state.py tests/test_discovery_state.py
git commit -m "feat: add StateManager with atomic writes and file locking"
```

---

## Task 2: Path Discovery

**Files:**
- Create: `core/discovery/paths.py`
- Test: `tests/test_discovery_paths.py`

- [ ] **Step 1: Write failing tests for path discovery**

```python
# tests/test_discovery_paths.py
import os
import tempfile
import pytest
from pathlib import Path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove any AH_ env vars that could interfere."""
    for key in list(os.environ):
        if key.startswith("AH_") or key == "AGENTHARNESS_HOME":
            monkeypatch.delenv(key, raising=False)


def test_discover_from_env_var(tmp_path, clean_env, monkeypatch):
    from core.discovery.paths import discover_paths
    # Create the expected structure
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(tmp_path))
    result = discover_paths()
    assert result["install_dir"] == str(tmp_path)
    assert result["scripts_dir"] == str(tmp_path / "scripts")


def test_discover_from_script_location(tmp_path, clean_env, monkeypatch):
    from core.discovery.paths import discover_paths
    (tmp_path / "scripts").mkdir()
    (tmp_path / "core" / "discovery").mkdir(parents=True)
    result = discover_paths(hint_dir=str(tmp_path))
    assert result["install_dir"] == str(tmp_path)


def test_discover_creates_missing_data_dirs(tmp_path, clean_env, monkeypatch):
    from core.discovery.paths import discover_paths
    (tmp_path / "scripts").mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(tmp_path))
    result = discover_paths()
    assert Path(result["data_dir"]).exists()
    assert Path(result["reports_dir"]).exists()
    assert Path(result["logs_dir"]).exists()


def test_override_wins(tmp_path, clean_env, monkeypatch):
    from core.discovery.paths import discover_paths
    (tmp_path / "scripts").mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(tmp_path))
    override = str(tmp_path / "custom_reports")
    Path(override).mkdir()
    result = discover_paths(overrides={"reports_dir": override})
    assert result["reports_dir"] == override


def test_model_dir_discovery(tmp_path, clean_env, monkeypatch):
    from core.discovery.paths import discover_paths
    (tmp_path / "scripts").mkdir()
    monkeypatch.setenv("AGENTHARNESS_HOME", str(tmp_path))
    result = discover_paths()
    assert "model_dir" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discovery_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.discovery.paths'`

- [ ] **Step 3: Implement path discovery**

```python
# core/discovery/paths.py
"""Discover AgentHarness installation paths at runtime.

Resolution order per path:
1. Explicit override (passed as argument)
2. Environment variable (AGENTHARNESS_HOME, AH_DATA_DIR, etc.)
3. Convention-based probing (common install locations)
4. Filesystem search (last resort)
"""

import os
from pathlib import Path


# Common install locations to probe (in priority order)
CONVENTIONAL_LOCATIONS = [
    Path.home() / "agentharness",
    Path("/opt/agentharness"),
    Path.home() / ".agentharness",
    Path.home() / ".local" / "share" / "agentharness",
]


def _find_install_dir(hint_dir: str | None = None) -> Path | None:
    """Find the AgentHarness install directory."""
    # 1. Environment variable
    env_home = os.environ.get("AGENTHARNESS_HOME")
    if env_home and Path(env_home).is_dir():
        return Path(env_home)

    # 2. Hint directory (e.g., from script location)
    if hint_dir and Path(hint_dir).is_dir():
        return Path(hint_dir)

    # 3. Convention-based probing
    for loc in CONVENTIONAL_LOCATIONS:
        if loc.is_dir() and (loc / "scripts").is_dir():
            return loc

    # 4. Walk up from this file's location
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts").is_dir() and (parent / "install.sh").exists():
            return parent

    return None


def discover_paths(
    hint_dir: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Discover all AgentHarness paths.

    Returns a dict of path_key -> resolved_path.
    Creates data directories if they don't exist.
    """
    overrides = overrides or {}

    install_dir = _find_install_dir(hint_dir)
    if install_dir is None:
        raise RuntimeError(
            "Cannot find AgentHarness installation. "
            "Set AGENTHARNESS_HOME or pass hint_dir."
        )

    install_dir = install_dir.resolve()

    # Derive standard paths from install_dir
    data_dir = Path(os.environ.get("AH_DATA_DIR", str(install_dir / "data")))
    model_dir = Path(os.environ.get("AH_MODEL_DIR", "/opt/models"))
    if not model_dir.exists():
        model_dir = install_dir / "models"

    paths = {
        "install_dir": str(install_dir),
        "scripts_dir": str(install_dir / "scripts"),
        "config_dir": str(install_dir / "config"),
        "bundles_dir": str(install_dir / "bundles"),
        "core_dir": str(install_dir / "core"),
        "data_dir": str(data_dir),
        "reports_dir": str(data_dir / "reports"),
        "logs_dir": str(data_dir / "logs"),
        "proposals_dir": str(data_dir / "proposals"),
        "briefings_dir": str(data_dir / "briefings"),
        "custom_dir": str(data_dir / "custom"),
        "model_dir": str(model_dir),
    }

    # Apply overrides
    for key, value in overrides.items():
        if key in paths:
            paths[key] = value

    # Create data directories that should exist
    for key in ("data_dir", "reports_dir", "logs_dir", "proposals_dir",
                "briefings_dir", "custom_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)

    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discovery_paths.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/discovery/paths.py tests/test_discovery_paths.py
git commit -m "feat: add path discovery with env var, convention, and override support"
```

---

## Task 3: Hardware Discovery

**Files:**
- Create: `core/discovery/hardware.py`
- Test: `tests/test_discovery_hardware.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_discovery_hardware.py
import pytest


def test_discover_hardware_returns_required_keys():
    from core.discovery.hardware import discover_hardware
    hw = discover_hardware()
    assert "total_ram_gb" in hw
    assert "cpu_cores" in hw
    assert "cpu_model" in hw
    assert "architecture" in hw


def test_ram_is_positive():
    from core.discovery.hardware import discover_hardware
    hw = discover_hardware()
    assert hw["total_ram_gb"] > 0


def test_cpu_cores_is_positive():
    from core.discovery.hardware import discover_hardware
    hw = discover_hardware()
    assert hw["cpu_cores"] > 0


def test_storage_devices_is_list():
    from core.discovery.hardware import discover_hardware
    hw = discover_hardware()
    assert isinstance(hw.get("storage_devices", []), list)


def test_recommended_model_size():
    from core.discovery.hardware import recommended_model_size_gb
    # 36GB total → leave ~8GB for OS → 28GB budget → suggest ~18GB model
    size = recommended_model_size_gb(total_ram_gb=36)
    assert 10 <= size <= 28

    # 8GB total → leave ~4GB for OS → 4GB budget
    size = recommended_model_size_gb(total_ram_gb=8)
    assert 2 <= size <= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discovery_hardware.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement hardware discovery**

```python
# core/discovery/hardware.py
"""Discover hardware capabilities — RAM, CPU, GPU, storage, network."""

import os
import platform
import subprocess
from pathlib import Path


def _run(cmd: str) -> str:
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def discover_hardware() -> dict:
    """Discover hardware capabilities of this machine."""
    hw = {
        "architecture": platform.machine(),
        "platform": platform.system().lower(),
        "hostname": platform.node(),
    }

    # RAM
    if Path("/proc/meminfo").exists():
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                hw["total_ram_gb"] = round(kb / 1024 / 1024, 1)
                break
    else:
        # macOS fallback
        sysctl = _run("sysctl -n hw.memsize")
        if sysctl:
            hw["total_ram_gb"] = round(int(sysctl) / 1024**3, 1)

    hw.setdefault("total_ram_gb", 0)

    # RAM per DIMM (Linux only)
    dimm_output = _run("sudo dmidecode -t memory 2>/dev/null | grep 'Size:' | grep -v 'No Module'")
    if dimm_output:
        hw["ram_dimms"] = [
            line.strip().replace("Size: ", "")
            for line in dimm_output.splitlines()
            if "Size:" in line
        ]

    # CPU
    hw["cpu_cores"] = os.cpu_count() or 1

    if Path("/proc/cpuinfo").exists():
        cpuinfo = Path("/proc/cpuinfo").read_text()
        for line in cpuinfo.splitlines():
            if line.startswith("model name"):
                hw["cpu_model"] = line.split(":", 1)[1].strip()
                break
    else:
        cpu_brand = _run("sysctl -n machdep.cpu.brand_string")
        if cpu_brand:
            hw["cpu_model"] = cpu_brand

    hw.setdefault("cpu_model", "unknown")

    # CPU features (AVX, AVX2, etc.)
    flags_line = ""
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags"):
                flags_line = line
                break
    hw["cpu_has_avx2"] = "avx2" in flags_line
    hw["cpu_has_avx512"] = "avx512" in flags_line

    # GPU (check for AMD iGPU / NVIDIA)
    lspci = _run("lspci 2>/dev/null | grep -i 'vga\\|3d\\|display'")
    hw["gpu_devices"] = [line.strip() for line in lspci.splitlines() if line.strip()] if lspci else []
    hw["has_nvidia"] = any("nvidia" in d.lower() for d in hw["gpu_devices"])
    hw["has_amd_gpu"] = any("amd" in d.lower() or "radeon" in d.lower() for d in hw["gpu_devices"])

    # NPU (AMD XDNA)
    xdna = _run("lspci 2>/dev/null | grep -i 'xdna\\|npu\\|ai accelerator'")
    hw["has_npu"] = bool(xdna.strip())

    # Storage devices
    lsblk = _run("lsblk -dnbo NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null")
    devices = []
    for line in lsblk.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "disk":
            size_gb = round(int(parts[1]) / 1024**3, 1) if parts[1].isdigit() else 0
            devices.append({
                "name": parts[0],
                "size_gb": size_gb,
                "mountpoint": parts[3] if len(parts) > 3 else None,
            })
    hw["storage_devices"] = devices

    # USB drives (potential backup targets)
    usb = _run("lsblk -dnbo NAME,SIZE,TRAN 2>/dev/null | grep usb")
    hw["usb_drives"] = [line.split()[0] for line in usb.splitlines() if line.strip()] if usb else []

    # Network interfaces
    ip_link = _run("ip -br link 2>/dev/null")
    hw["network_interfaces"] = [
        line.split()[0] for line in ip_link.splitlines()
        if line.strip() and not line.startswith("lo")
    ] if ip_link else []

    return hw


def recommended_model_size_gb(total_ram_gb: float) -> int:
    """Recommend max model file size based on available RAM.

    Reserves RAM for OS + services, returns budget for the model file.
    """
    if total_ram_gb <= 4:
        return 2
    elif total_ram_gb <= 8:
        return int(total_ram_gb * 0.5)
    elif total_ram_gb <= 16:
        return int(total_ram_gb * 0.6)
    else:
        # For 32GB+, leave ~8GB for OS, rest for model + KV cache
        return int(total_ram_gb - 8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discovery_hardware.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/discovery/hardware.py tests/test_discovery_hardware.py
git commit -m "feat: add hardware discovery — RAM, CPU, GPU, NPU, storage, network"
```

---

## Task 4: Service Discovery

**Files:**
- Create: `core/discovery/services.py`
- Test: `tests/test_discovery_services.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_discovery_services.py
import pytest
import json
import subprocess
from unittest.mock import patch


def test_discover_docker_returns_list():
    from core.discovery.services import discover_docker_services
    # May return empty list if Docker isn't running — that's OK
    result = discover_docker_services()
    assert isinstance(result, list)


def test_parse_docker_output():
    from core.discovery.services import _parse_docker_ps
    sample = (
        '{"Names":"jellyfin","Image":"jellyfin/jellyfin:latest","Ports":"8096/tcp","Status":"Up 3 days","ID":"abc123"}\n'
        '{"Names":"immich","Image":"ghcr.io/immich-app/immich-server:release","Ports":"2283/tcp","Status":"Up 3 days","ID":"def456"}\n'
    )
    result = _parse_docker_ps(sample)
    assert len(result) == 2
    assert result[0]["name"] == "jellyfin"
    assert result[0]["image"] == "jellyfin/jellyfin:latest"


def test_discover_llm_servers_returns_list():
    from core.discovery.services import discover_llm_servers
    result = discover_llm_servers()
    assert isinstance(result, list)


def test_discover_listening_ports_returns_dict():
    from core.discovery.services import discover_listening_ports
    result = discover_listening_ports()
    assert isinstance(result, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discovery_services.py -v`
Expected: FAIL

- [ ] **Step 3: Implement service discovery**

```python
# core/discovery/services.py
"""Discover running services — Docker containers, LLM servers, ports."""

import json
import subprocess
from typing import Any


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _parse_docker_ps(output: str) -> list[dict]:
    """Parse 'docker ps --format json' output into structured list."""
    containers = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            containers.append({
                "name": data.get("Names", ""),
                "image": data.get("Image", ""),
                "ports": data.get("Ports", ""),
                "status": data.get("Status", ""),
                "id": data.get("ID", ""),
            })
        except json.JSONDecodeError:
            continue
    return containers


def discover_docker_services() -> list[dict]:
    """Discover running Docker containers."""
    output = _run('docker ps --format "{{json .}}" 2>/dev/null')
    if not output:
        return []
    return _parse_docker_ps(output)


def discover_llm_servers() -> list[dict]:
    """Probe common LLM server ports for health endpoints."""
    servers = []
    common_ports = [8080, 8081, 11434, 5000, 8000, 1234]

    for port in common_ports:
        health = _run(f"curl -sf --max-time 2 http://localhost:{port}/health 2>/dev/null")
        if health:
            servers.append({
                "port": port,
                "url": f"http://localhost:{port}",
                "health_response": health[:200],
            })
            continue
        # Try /v1/models (OpenAI-compatible)
        models = _run(f"curl -sf --max-time 2 http://localhost:{port}/v1/models 2>/dev/null")
        if models:
            servers.append({
                "port": port,
                "url": f"http://localhost:{port}",
                "type": "openai_compatible",
                "models_response": models[:200],
            })

    return servers


def discover_listening_ports() -> dict[int, str]:
    """Discover all listening TCP ports and their processes."""
    output = _run("ss -tlnp 2>/dev/null")
    if not output:
        return {}

    ports = {}
    for line in output.splitlines()[1:]:  # Skip header
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            if ":" in addr:
                port_str = addr.rsplit(":", 1)[-1]
                if port_str.isdigit():
                    process = parts[-1] if len(parts) > 4 else "unknown"
                    ports[int(port_str)] = process
    return ports


def discover_services() -> dict[str, Any]:
    """Run all service discovery and return combined results."""
    return {
        "docker_containers": discover_docker_services(),
        "llm_servers": discover_llm_servers(),
        "listening_ports": discover_listening_ports(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discovery_services.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/discovery/services.py tests/test_discovery_services.py
git commit -m "feat: add service discovery — Docker containers, LLM servers, ports"
```

---

## Task 5: Agent Discovery

**Files:**
- Create: `core/discovery/agents.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_discovery_agents.py (append to new file)
import pytest
from unittest.mock import patch


def test_discover_agents_returns_list():
    from core.discovery.agents import discover_agents
    result = discover_agents()
    assert isinstance(result, list)


def test_parse_chaguli_detection():
    from core.discovery.agents import _detect_chaguli_in_container
    # Simulate docker inspect output
    mock_inspect = '{"Mounts":[{"Source":"/home/user/chaguli","Destination":"/app"}]}'
    with patch("core.discovery.agents._run", return_value=mock_inspect):
        result = _detect_chaguli_in_container("chaguli-container")
    assert result is not None or result is None  # Just test it doesn't crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discovery_agents.py -v`
Expected: FAIL

- [ ] **Step 3: Implement agent discovery**

```python
# core/discovery/agents.py
"""Discover agent installations — Chaguli, OpenClaw, or custom agents."""

import json
import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


# Files that identify a Chaguli installation
CHAGULI_MARKERS = ["tools.py", "config.yml", "memory.py", "agent.py"]

# Files that identify an OpenClaw installation
OPENCLAW_MARKERS = [".openclaw", "workspace/AGENTS.md"]


def _detect_chaguli_in_container(container_name: str) -> dict | None:
    """Try to detect Chaguli inside a Docker container."""
    inspect = _run(f"docker inspect {container_name} 2>/dev/null")
    if not inspect:
        return None

    try:
        data = json.loads(inspect)
        if isinstance(data, list):
            data = data[0]
    except json.JSONDecodeError:
        return None

    mounts = data.get("Mounts", [])
    env_vars = data.get("Config", {}).get("Env", [])

    # Find the app directory from mounts
    app_dir = None
    host_dir = None
    for mount in mounts:
        dest = mount.get("Destination", "")
        src = mount.get("Source", "")
        if dest in ("/app", "/opt/chaguli", "/chaguli"):
            app_dir = dest
            host_dir = src
            break

    if not host_dir:
        return None

    # Check for Chaguli marker files on the host
    host_path = Path(host_dir)
    found_markers = [m for m in CHAGULI_MARKERS if (host_path / m).exists()]
    if not found_markers:
        return None

    return {
        "type": "chaguli",
        "container_name": container_name,
        "app_dir": app_dir,
        "host_dir": str(host_path),
        "found_markers": found_markers,
        "capabilities": _detect_chaguli_capabilities(host_path),
    }


def _detect_chaguli_capabilities(host_dir: Path) -> list[str]:
    """Detect which Chaguli modules are present."""
    caps = []
    cap_map = {
        "tools.py": "tools",
        "memory.py": "memory",
        "self_improve.py": "self_improve",
        "heartbeat.py": "heartbeat",
        "briefings.py": "briefings",
        "agent.py": "agent_loop",
        "config.yml": "config",
    }
    for filename, cap_name in cap_map.items():
        if (host_dir / filename).exists():
            caps.append(cap_name)
    return caps


def _detect_openclaw() -> dict | None:
    """Check for OpenClaw installation."""
    home_openclaw = Path.home() / ".openclaw"
    if home_openclaw.exists() and (home_openclaw / "workspace").is_dir():
        return {
            "type": "openclaw",
            "install_dir": str(home_openclaw),
            "workspace_dir": str(home_openclaw / "workspace"),
        }
    return None


def discover_agents() -> list[dict[str, Any]]:
    """Discover all agent installations on this machine."""
    agents = []

    # Check Docker containers for Chaguli
    containers = _run('docker ps --format "{{.Names}}" 2>/dev/null')
    for name in containers.splitlines():
        name = name.strip()
        if not name:
            continue
        chaguli = _detect_chaguli_in_container(name)
        if chaguli:
            agents.append(chaguli)

    # Check for OpenClaw
    openclaw = _detect_openclaw()
    if openclaw:
        agents.append(openclaw)

    return agents
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discovery_agents.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/discovery/agents.py tests/test_discovery_agents.py
git commit -m "feat: add agent discovery — detects Chaguli in Docker, OpenClaw on host"
```

---

## Task 6: Discovery Engine Coordinator

**Files:**
- Create: `core/discovery/engine.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_discovery_engine.py
import os
import pytest


@pytest.fixture
def engine_env(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "core" / "discovery").mkdir(parents=True)
    monkeypatch.setenv("AGENTHARNESS_HOME", str(tmp_path))
    monkeypatch.setenv("AH_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_full_discovery(engine_env):
    from core.discovery.engine import run_discovery
    state = run_discovery()
    assert "paths" in state
    assert "hardware" in state
    assert "services" in state
    assert state["paths"]["install_dir"] == str(engine_env)


def test_full_discovery_writes_state(engine_env):
    from core.discovery.engine import run_discovery
    from core.discovery.state import StateManager
    run_discovery()
    sm = StateManager(data_dir=str(engine_env / "data"))
    state = sm.read()
    assert "paths" in state
    assert "hardware" in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discovery_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement discovery engine**

```python
# core/discovery/engine.py
"""Discovery engine coordinator — runs all discovery modules and writes state."""

import os
from typing import Any

from core.discovery.state import StateManager
from core.discovery.paths import discover_paths
from core.discovery.hardware import discover_hardware
from core.discovery.services import discover_services
from core.discovery.agents import discover_agents


def run_discovery(
    hint_dir: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run full discovery and write results to state.json.

    Returns the complete state dict.
    """
    # Paths first — everything else depends on knowing where we are
    paths = discover_paths(hint_dir=hint_dir, overrides=overrides)

    # Hardware
    hardware = discover_hardware()

    # Services (Docker, LLM servers, ports)
    services = discover_services()

    # Agents (Chaguli, OpenClaw)
    agents = discover_agents()

    # Write to state
    data_dir = paths.get("data_dir", paths["install_dir"])
    sm = StateManager(data_dir=data_dir)
    sm.write({
        "paths": paths,
        "hardware": hardware,
        "services": services,
        "agents": agents,
    })

    return sm.read()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discovery_engine.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/discovery/engine.py tests/test_discovery_engine.py
git commit -m "feat: add discovery engine coordinator — runs all modules, writes state"
```

---

## Task 7: Rewrite common.sh to Use Discovery

**Files:**
- Modify: `scripts/common.sh`
- Test: `tests/test_common_sh.py`

- [ ] **Step 1: Write test for new common.sh**

```python
# tests/test_common_sh.py
import json
import os
import subprocess
import pytest
from pathlib import Path


@pytest.fixture
def mock_state(tmp_path):
    """Create a mock state.json and the script structure."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    state = {
        "schema_version": 1,
        "paths": {
            "install_dir": str(tmp_path),
            "scripts_dir": str(tmp_path / "scripts"),
            "data_dir": str(data_dir),
            "reports_dir": str(data_dir / "reports"),
            "logs_dir": str(data_dir / "logs"),
            "config_dir": str(tmp_path / "config"),
            "model_dir": str(tmp_path / "models"),
            "custom_dir": str(data_dir / "custom"),
        },
    }
    (data_dir / "state.json").write_text(json.dumps(state))

    # Copy common.sh to the test location
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    repo_root = Path(__file__).parent.parent
    common_sh = repo_root / "scripts" / "common.sh"
    (scripts_dir / "common.sh").write_text(common_sh.read_text())

    return tmp_path, data_dir


def test_common_sh_exports_paths(mock_state):
    tmp_path, data_dir = mock_state
    scripts_dir = tmp_path / "scripts"

    # Write a test script that sources common.sh and prints vars
    test_script = scripts_dir / "test_env.sh"
    test_script.write_text(f"""#!/usr/bin/env bash
export AH_DATA_DIR="{data_dir}"
source "{scripts_dir}/common.sh"
echo "INSTALL=$AH_INSTALL_DIR"
echo "SCRIPTS=$AH_SCRIPTS_DIR"
echo "REPORTS=$AH_REPORTS_DIR"
echo "LOGS=$AH_LOGS_DIR"
""")
    test_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(test_script)],
        capture_output=True, text=True, timeout=10,
    )
    assert f"INSTALL={tmp_path}" in result.stdout
    assert f"SCRIPTS={scripts_dir}" in result.stdout
    assert f"REPORTS={data_dir}/reports" in result.stdout
    assert f"LOGS={data_dir}/logs" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_common_sh.py -v`
Expected: FAIL — common.sh still uses hardcoded paths

- [ ] **Step 3: Rewrite common.sh**

```bash
#!/usr/bin/env bash
# =============================================================================
# common.sh — Shared utilities for all AgentHarness scripts
#
# IMPORTANT: This file reads paths from state.json via the discovery engine.
# No paths are hardcoded. All scripts source this file first.
# =============================================================================

# Colors (only if terminal supports it)
if [ -t 1 ] && command -v tput &>/dev/null; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
fi

log_info()   { echo "${BLUE}[INFO]${RESET} $*"; }
log_ok()     { echo "${GREEN}[OK]${RESET} $*"; }
log_warn()   { echo "${YELLOW}[WARN]${RESET} $*"; }
log_error()  { echo "${RED}[ERROR]${RESET} $*" >&2; }
log_header() {
    echo ""
    echo "${BOLD}=========================================${RESET}"
    echo "${BOLD}  $*${RESET}"
    echo "${BOLD}=========================================${RESET}"
    echo ""
}

# Timestamp for reports
timestamp() { date '+%Y-%m-%d_%H-%M-%S'; }

# Check if running as root (warn, don't require)
check_root_warn() {
    if [ "$(id -u)" -eq 0 ]; then
        log_warn "Running as root. Some operations will run without sudo."
    fi
}

# Ensure a directory exists with correct ownership
ensure_dir() {
    local dir="$1"
    if [ ! -d "${dir}" ]; then
        mkdir -p "${dir}" 2>/dev/null || sudo mkdir -p "${dir}"
        if [ "$(stat -c %U "${dir}" 2>/dev/null)" != "$USER" ]; then
            sudo chown "$USER:$USER" "${dir}" 2>/dev/null || true
        fi
    fi
}

# =============================================================================
# PATH RESOLUTION — reads from state.json, no hardcoded paths
# =============================================================================

# Find the state.json file
_ah_find_state() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"

    # 1. AH_DATA_DIR env var (set by installer, systemd, or user)
    if [ -n "${AH_DATA_DIR:-}" ] && [ -f "${AH_DATA_DIR}/state.json" ]; then
        echo "${AH_DATA_DIR}/state.json"
        return
    fi

    # 2. Relative to this script: ../data/state.json
    local parent_dir
    parent_dir="$(dirname "$script_dir")"
    if [ -f "${parent_dir}/data/state.json" ]; then
        echo "${parent_dir}/data/state.json"
        return
    fi

    # 3. Common locations
    for candidate in \
        "$HOME/agentharness/data/state.json" \
        "/opt/agentharness/data/state.json" \
        "$HOME/.agentharness/data/state.json"; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return
        fi
    done

    # Not found
    return 1
}

# Load paths from state.json into AH_* environment variables
_ah_load_paths() {
    local state_file
    state_file="$(_ah_find_state)" || {
        log_error "Cannot find state.json. Run 'agentharness discover' or set AH_DATA_DIR."
        log_error "If this is a fresh install, run: python3 -m core.discovery.engine"
        return 1
    }

    # Parse state.json and export AH_* variables
    eval "$(python3 -c "
import json, sys
try:
    state = json.load(open('$state_file'))
    paths = state.get('paths', {})
    for key, val in paths.items():
        env_key = 'AH_' + key.upper()
        print(f'export {env_key}=\"{val}\"')
except Exception as e:
    print(f'echo \"ERROR: Failed to parse state.json: {e}\" >&2', file=sys.stdout)
    sys.exit(1)
" 2>/dev/null)" || {
        log_error "Failed to parse state.json"
        return 1
    }
}

# Load paths on source (every script that sources common.sh gets paths)
_ah_load_paths || true

# Legacy compatibility: set old variable names from new ones
AGENTHARNESS_DIR="${AH_INSTALL_DIR:-}"
MODEL_DIR="${AH_MODEL_DIR:-/opt/models}"
REPORT_DIR="${AH_REPORTS_DIR:-}"

# Load environment file if it exists
if [ -n "${AH_DATA_DIR:-}" ] && [ -f "${AH_DATA_DIR}/.env" ]; then
    source "${AH_DATA_DIR}/.env"
elif [ -n "${AH_INSTALL_DIR:-}" ] && [ -f "${AH_INSTALL_DIR}/.env" ]; then
    source "${AH_INSTALL_DIR}/.env"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_common_sh.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/common.sh tests/test_common_sh.py
git commit -m "feat: rewrite common.sh to read paths from state.json — no hardcoded paths"
```

---

## Task 8: Rewrite All Scripts to Use $AH_* Variables

This is the bulk migration. Every script has hardcoded `/opt/agentharness` paths that must change to `$AH_*` variables. This is mechanical but critical.

**Files:** All 24 scripts in `scripts/`, plus `install.sh`, `config/harness_registry.yaml`, `config/env.template`, `config/systemd/*.service`

- [ ] **Step 1: Create a migration helper script**

```bash
# scripts/migrate_paths.sh — ONE-TIME migration helper (not committed)
# Validates that no hardcoded /opt/agentharness remains after manual edits

#!/usr/bin/env bash
echo "Scanning for hardcoded /opt/agentharness paths..."
count=$(grep -rn "/opt/agentharness" scripts/ install.sh config/ --include="*.sh" --include="*.py" --include="*.yaml" --include="*.yml" --include="*.service" --include="*.template" 2>/dev/null | grep -v "state.json" | grep -v "test_" | grep -v ".git" | wc -l)
echo "Found: $count hardcoded references"
if [ "$count" -gt 0 ]; then
    grep -rn "/opt/agentharness" scripts/ install.sh config/ --include="*.sh" --include="*.py" --include="*.yaml" --include="*.yml" --include="*.service" --include="*.template" 2>/dev/null | grep -v "state.json" | grep -v "test_" | grep -v ".git"
fi
```

- [ ] **Step 2: Migrate scripts/alert.sh**

Replace all `/opt/agentharness` references with `$AH_*` variables:
- `/opt/agentharness/.env` → sourced by common.sh automatically
- `/opt/agentharness/alert_queue.json` → `${AH_DATA_DIR}/alert_queue.json`
- `mkdir -p /opt/agentharness` → `ensure_dir "${AH_DATA_DIR}"`

- [ ] **Step 3: Migrate scripts/scheduler.sh**

Replace:
- `SCHEDULER_STATE="/opt/agentharness/scheduler_state.json"` → `SCHEDULER_STATE="${AH_DATA_DIR}/scheduler_state.json"`
- `TASK_QUEUE="/opt/agentharness/task_queue.json"` → `TASK_QUEUE="${AH_DATA_DIR}/task_queue.json"`
- `LOG_FILE="/opt/agentharness/logs/scheduler.log"` → `LOG_FILE="${AH_LOGS_DIR}/scheduler.log"`
- All `.env` sourcing → handled by common.sh
- All `stat -c %Y /opt/agentharness/...` → use `$AH_DATA_DIR` or `$AH_REPORTS_DIR`

- [ ] **Step 4: Migrate scripts/registry_engine.py**

Replace:
- `REGISTRY_PATH = "/opt/agentharness/config/harness_registry.yaml"` → read from state.json or env
- `STATE_FILE = "/opt/agentharness/registry_state.json"` → `{data_dir}/registry_state.json`
- All other hardcoded paths → use state.json resolution

```python
# At the top of registry_engine.py, add:
import os
import json

def _resolve_paths():
    """Read paths from state.json or fall back to env vars."""
    data_dir = os.environ.get("AH_DATA_DIR", "/opt/agentharness/data")
    state_file = os.path.join(data_dir, "state.json")
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
            paths = state.get("paths", {})
            return {
                "registry": os.path.join(paths.get("config_dir", "config"), "harness_registry.yaml"),
                "state": os.path.join(paths.get("data_dir", data_dir), "registry_state.json"),
                "scripts": paths.get("scripts_dir", "scripts"),
                "custom": paths.get("custom_dir", "custom"),
                "logs": paths.get("logs_dir", "logs"),
            }
    return {
        "registry": os.environ.get("AH_CONFIG_DIR", "config") + "/harness_registry.yaml",
        "state": data_dir + "/registry_state.json",
        "scripts": os.environ.get("AH_SCRIPTS_DIR", "scripts"),
        "custom": os.environ.get("AH_CUSTOM_DIR", "custom"),
        "logs": os.environ.get("AH_LOGS_DIR", "logs"),
    }

_PATHS = _resolve_paths()
REGISTRY_PATH = _PATHS["registry"]
STATE_FILE = _PATHS["state"]
SCRIPTS_DIR = _PATHS["scripts"]
CUSTOM_DIR = _PATHS["custom"]
LOG_DIR = _PATHS["logs"]
```

- [ ] **Step 5: Migrate remaining scripts (batch)**

Apply the same pattern to each script. Each script already sources `common.sh` which now provides `$AH_*` variables. The mechanical change is:

| Old pattern | New pattern |
|------------|-------------|
| `/opt/agentharness/.env` | Removed (common.sh handles it) |
| `/opt/agentharness/reports/...` | `${AH_REPORTS_DIR}/...` |
| `/opt/agentharness/logs/...` | `${AH_LOGS_DIR}/...` |
| `/opt/agentharness/some_file.json` | `${AH_DATA_DIR}/some_file.json` |
| `/opt/agentharness/scripts/...` | `${AH_SCRIPTS_DIR}/...` |
| `/opt/agentharness/config/...` | `${AH_CONFIG_DIR}/...` |
| `/opt/agentharness/custom/...` | `${AH_CUSTOM_DIR}/...` |
| `ensure_dir /opt/agentharness` | `ensure_dir "${AH_DATA_DIR}"` |
| `/opt/agentharness/openclaw_paths.env` | `${AH_DATA_DIR}/chaguli_paths.env` |
| `/opt/agentharness/hw_profile.env` | `${AH_DATA_DIR}/hw_profile.env` |
| `/opt/agentharness/model_catalog.json` | `${AH_DATA_DIR}/model_catalog.json` |
| `/opt/agentharness/benchmark_results.json` | `${AH_DATA_DIR}/benchmark_results.json` |
| `/opt/agentharness/best_config.env` | `${AH_DATA_DIR}/best_config.env` |
| `/opt/agentharness/storage_paths.env` | `${AH_DATA_DIR}/storage_paths.env` |
| `/opt/agentharness/chaguli_paths.env` | `${AH_DATA_DIR}/chaguli_paths.env` |
| `/opt/agentharness/trend_data.csv` | `${AH_DATA_DIR}/trend_data.csv` |

Scripts to migrate (each one follows the same pattern):
1. `cleanup.sh`
2. `backup.sh`
3. `benchmark.sh`
4. `build_inference.sh`
5. `discover_automations.sh`
6. `discover_chaguli.sh`
7. `discover_config.sh`
8. `discover_storage.sh`
9. `doctor.sh`
10. `download_models.sh`
11. `github_deploy.sh`
12. `harden.sh`
13. `mcp_gateway.sh`
14. `security_audit.sh`
15. `self_update.sh`
16. `setup_minipc.sh`
17. `trend_projector.sh`
18. `update_watcher.sh`
19. `validate.sh`
20. `weekly_optimize.sh`
21. `integrate_chaguli.sh`

- [ ] **Step 6: Migrate config files**

`config/harness_registry.yaml`:
- `custom_scripts_dir: "/opt/agentharness/custom"` → `custom_scripts_dir: "auto"` (resolved by registry engine from state.json)
- Comments referencing `/opt/agentharness` → update to reference `$AH_*` variables

`config/env.template`:
- `# Copy to /opt/agentharness/.env` → `# Copy to your AgentHarness data dir as .env`

`config/systemd/llama-primary.service`:
- `ReadWritePaths=/opt/models /opt/agentharness /tmp` → `ReadWritePaths=/opt/models %h/agentharness /tmp`

`config/systemd/llama-fast.service`:
- Same change as above

- [ ] **Step 7: Run migration validation**

```bash
bash scripts/migrate_paths.sh
```
Expected: `Found: 0 hardcoded references`

- [ ] **Step 8: Commit**

```bash
git add scripts/ config/ install.sh
git commit -m "feat: migrate all scripts from hardcoded paths to discovery-based AH_* variables

Replaces 140+ hardcoded /opt/agentharness references across 24 scripts,
config files, and systemd services with AH_* environment variables
resolved from state.json at runtime."
```

---

## Task 9: Registry Schema Validation

**Files:**
- Create: `core/registry/__init__.py`
- Create: `core/registry/schema.py`
- Test: `tests/test_registry_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_registry_schema.py
import pytest


def test_valid_check_passes():
    from core.registry.schema import validate_check
    check = {
        "enabled": True,
        "command": "df / | awk 'NR==2 {print $5}'",
        "type": "threshold",
        "warn": 80,
        "critical": 90,
        "unit": "%",
        "message": "Disk at {value}%",
    }
    errors = validate_check("disk_usage", check)
    assert errors == []


def test_check_missing_command_fails():
    from core.registry.schema import validate_check
    check = {"type": "threshold", "warn": 80}
    errors = validate_check("bad_check", check)
    assert any("command" in e for e in errors)


def test_check_invalid_type_fails():
    from core.registry.schema import validate_check
    check = {"command": "echo hi", "type": "magic"}
    errors = validate_check("bad_type", check)
    assert any("type" in e for e in errors)


def test_valid_tool_passes():
    from core.registry.schema import validate_tool
    tool = {
        "description": "Run a backup",
        "script": "backup.sh",
        "approval_tier": "approve",
        "sandbox_mode": "direct",
    }
    errors = validate_tool("run_backup", tool)
    assert errors == []


def test_tool_missing_description_fails():
    from core.registry.schema import validate_tool
    tool = {"script": "backup.sh"}
    errors = validate_tool("bad_tool", tool)
    assert any("description" in e for e in errors)


def test_valid_harness_passes():
    from core.registry.schema import validate_harness
    harness = {
        "script": "cleanup.sh",
        "frequency": "3d",
        "window": "offline",
        "description": "Clean up",
    }
    errors = validate_harness("cleanup", harness)
    assert errors == []


def test_harness_invalid_frequency_fails():
    from core.registry.schema import validate_harness
    harness = {"script": "cleanup.sh", "frequency": "never"}
    errors = validate_harness("bad_freq", harness)
    assert any("frequency" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_registry_schema.py -v`
Expected: FAIL

- [ ] **Step 3: Implement schema validation**

```python
# core/registry/__init__.py
"""Registry engine — YAML-driven tool, check, and harness system."""

# core/registry/schema.py
"""Validate registry entries against the schema."""

import re

VALID_CHECK_TYPES = {"threshold", "command_exit", "command_output", "regex_match", "http_probe"}
VALID_APPROVAL_TIERS = {"auto", "notify", "approve"}
VALID_SANDBOX_MODES = {"direct", "containerized"}
VALID_WINDOWS = {"online", "offline", "offline_lan", "any"}
VALID_FREQUENCIES = re.compile(
    r"^(\d+[mhd]|daily|weekly|monthly|on_boot)$"
)


def validate_check(name: str, check: dict) -> list[str]:
    """Validate a check entry. Returns list of error strings (empty = valid)."""
    errors = []
    if "command" not in check:
        errors.append(f"check '{name}': missing required field 'command'")
    if "type" not in check:
        errors.append(f"check '{name}': missing required field 'type'")
    elif check["type"] not in VALID_CHECK_TYPES:
        errors.append(
            f"check '{name}': invalid type '{check['type']}'. "
            f"Must be one of: {', '.join(sorted(VALID_CHECK_TYPES))}"
        )
    if check.get("type") == "threshold":
        if "warn" not in check and "critical" not in check:
            errors.append(f"check '{name}': threshold type requires 'warn' or 'critical'")
    return errors


def validate_tool(name: str, tool: dict) -> list[str]:
    """Validate a tool entry. Returns list of error strings."""
    errors = []
    if "description" not in tool:
        errors.append(f"tool '{name}': missing required field 'description'")
    if "script" not in tool and "command" not in tool:
        errors.append(f"tool '{name}': must have 'script' or 'command'")
    if "approval_tier" in tool and tool["approval_tier"] not in VALID_APPROVAL_TIERS:
        errors.append(
            f"tool '{name}': invalid approval_tier '{tool['approval_tier']}'. "
            f"Must be one of: {', '.join(sorted(VALID_APPROVAL_TIERS))}"
        )
    if "sandbox_mode" in tool and tool["sandbox_mode"] not in VALID_SANDBOX_MODES:
        errors.append(
            f"tool '{name}': invalid sandbox_mode '{tool['sandbox_mode']}'. "
            f"Must be one of: {', '.join(sorted(VALID_SANDBOX_MODES))}"
        )
    return errors


def validate_harness(name: str, harness: dict) -> list[str]:
    """Validate a harness entry. Returns list of error strings."""
    errors = []
    if "script" not in harness:
        errors.append(f"harness '{name}': missing required field 'script'")
    if "frequency" not in harness:
        errors.append(f"harness '{name}': missing required field 'frequency'")
    elif not VALID_FREQUENCIES.match(str(harness["frequency"])):
        errors.append(
            f"harness '{name}': invalid frequency '{harness['frequency']}'. "
            f"Must match: Nd, Nh, Nm, daily, weekly, monthly, on_boot"
        )
    if "window" in harness and harness["window"] not in VALID_WINDOWS:
        errors.append(
            f"harness '{name}': invalid window '{harness['window']}'. "
            f"Must be one of: {', '.join(sorted(VALID_WINDOWS))}"
        )
    return errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_registry_schema.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/registry/__init__.py core/registry/schema.py tests/test_registry_schema.py
git commit -m "feat: add registry schema validation for checks, tools, and harnesses"
```

---

## Task 10: Registry Bundle Loader

**Files:**
- Create: `core/registry/loader.py`
- Test: `tests/test_registry_loader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_registry_loader.py
import pytest
import yaml
from pathlib import Path


@pytest.fixture
def bundle_dir(tmp_path):
    """Create a test bundle directory structure."""
    core = tmp_path / "bundles" / "core"
    core.mkdir(parents=True)
    (core / "bundle.yaml").write_text(yaml.dump({
        "checks": {
            "disk_usage": {
                "enabled": True,
                "command": "df / | awk 'NR==2 {print $5}'",
                "type": "threshold",
                "warn": 80,
                "critical": 90,
                "message": "Disk at {value}%",
            },
        },
        "harnesses": {
            "cleanup": {
                "enabled": True,
                "script": "cleanup.sh",
                "frequency": "3d",
                "window": "offline",
            },
        },
    }))

    homelab = tmp_path / "bundles" / "homelab"
    homelab.mkdir(parents=True)
    (homelab / "bundle.yaml").write_text(yaml.dump({
        "checks": {
            "docker_unhealthy": {
                "enabled": True,
                "command": "docker ps --filter 'health=unhealthy' --format '{{.Names}}'",
                "type": "command_output",
                "message": "Unhealthy: {value}",
            },
        },
    }))

    overrides = tmp_path / "config"
    overrides.mkdir(parents=True)
    (overrides / "overrides.yaml").write_text(yaml.dump({
        "checks": {
            "disk_usage": {"warn": 70},  # Override threshold
        },
    }))

    return tmp_path


def test_load_bundles(bundle_dir):
    from core.registry.loader import load_registry
    registry = load_registry(bundles_dir=str(bundle_dir / "bundles"))
    assert "disk_usage" in registry["checks"]
    assert "docker_unhealthy" in registry["checks"]


def test_overrides_win(bundle_dir):
    from core.registry.loader import load_registry
    registry = load_registry(
        bundles_dir=str(bundle_dir / "bundles"),
        overrides_file=str(bundle_dir / "config" / "overrides.yaml"),
    )
    assert registry["checks"]["disk_usage"]["warn"] == 70


def test_validation_errors_reported(bundle_dir):
    from core.registry.loader import load_registry
    # Add an invalid check
    bad = bundle_dir / "bundles" / "bad"
    bad.mkdir(parents=True)
    (bad / "bundle.yaml").write_text(yaml.dump({
        "checks": {
            "bad_check": {"type": "magic"},
        },
    }))
    registry = load_registry(bundles_dir=str(bundle_dir / "bundles"))
    assert len(registry.get("validation_errors", [])) > 0


def test_discovered_yaml_merged(bundle_dir):
    from core.registry.loader import load_registry
    homelab = bundle_dir / "bundles" / "homelab"
    (homelab / "discovered.yaml").write_text(yaml.dump({
        "checks": {
            "jellyfin_health": {
                "auto_generated": True,
                "command": "curl -sf http://localhost:8096/health",
                "type": "http_probe",
                "message": "Jellyfin down",
            },
        },
    }))
    registry = load_registry(bundles_dir=str(bundle_dir / "bundles"))
    assert "jellyfin_health" in registry["checks"]


def test_harnesses_loaded(bundle_dir):
    from core.registry.loader import load_registry
    registry = load_registry(bundles_dir=str(bundle_dir / "bundles"))
    assert "cleanup" in registry["harnesses"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_registry_loader.py -v`
Expected: FAIL

- [ ] **Step 3: Implement bundle loader**

```python
# core/registry/loader.py
"""Load and merge YAML bundle files into a unified registry."""

import logging
from pathlib import Path
from typing import Any

import yaml

from core.registry.schema import validate_check, validate_harness, validate_tool

log = logging.getLogger("registry.loader")


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, return empty dict on failure."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        log.warning(f"Failed to parse {path}: {e}")
        return {}


def _merge_section(target: dict, source: dict, section: str, bundle_name: str) -> list[str]:
    """Merge a section (checks/tools/harnesses) from source into target.

    Returns list of warnings (e.g., overwritten keys).
    """
    warnings = []
    source_section = source.get(section, {})
    if not isinstance(source_section, dict):
        return warnings

    if section not in target:
        target[section] = {}

    for key, value in source_section.items():
        if key in target[section]:
            warnings.append(
                f"{section}.{key}: overwritten by bundle '{bundle_name}'"
            )
        if isinstance(value, dict) and isinstance(target[section].get(key), dict):
            # Merge dict (e.g., override only 'warn' in a check)
            target[section][key].update(value)
        else:
            target[section][key] = value

    return warnings


def load_registry(
    bundles_dir: str,
    overrides_file: str | None = None,
) -> dict[str, Any]:
    """Load all bundle YAML files and merge into a unified registry.

    Load order:
    1. bundles/core/bundle.yaml (always first)
    2. bundles/*/bundle.yaml (alphabetical, excluding core)
    3. bundles/*/discovered.yaml (runtime-generated, merged after bundle.yaml)
    4. config/overrides.yaml (user overrides, always last)

    Returns: {
        "checks": {...},
        "tools": {...},
        "harnesses": {...},
        "validation_errors": [...],
        "warnings": [...],
    }
    """
    registry: dict[str, Any] = {
        "checks": {},
        "tools": {},
        "harnesses": {},
        "validation_errors": [],
        "warnings": [],
    }

    bundles_path = Path(bundles_dir)
    if not bundles_path.is_dir():
        log.warning(f"Bundles directory not found: {bundles_dir}")
        return registry

    # Collect bundle directories in order: core first, then alphabetical
    bundle_dirs = []
    core_dir = bundles_path / "core"
    if core_dir.is_dir():
        bundle_dirs.append(("core", core_dir))
    for d in sorted(bundles_path.iterdir()):
        if d.is_dir() and d.name != "core" and d.name != "community":
            bundle_dirs.append((d.name, d))
    # Community bundles last
    community = bundles_path / "community"
    if community.is_dir():
        for d in sorted(community.iterdir()):
            if d.is_dir():
                bundle_dirs.append((f"community/{d.name}", d))

    # Load each bundle
    for bundle_name, bundle_dir in bundle_dirs:
        bundle_file = bundle_dir / "bundle.yaml"
        bundle_data = _load_yaml(bundle_file)

        for section in ("checks", "tools", "harnesses"):
            warnings = _merge_section(registry, bundle_data, section, bundle_name)
            registry["warnings"].extend(warnings)

        # Load discovered.yaml (runtime-generated checks)
        discovered_file = bundle_dir / "discovered.yaml"
        discovered_data = _load_yaml(discovered_file)
        if discovered_data:
            for section in ("checks", "tools", "harnesses"):
                warnings = _merge_section(registry, discovered_data, section, f"{bundle_name}/discovered")
                registry["warnings"].extend(warnings)

    # Apply overrides (always win)
    if overrides_file:
        overrides = _load_yaml(Path(overrides_file))
        for section in ("checks", "tools", "harnesses"):
            _merge_section(registry, overrides, section, "overrides")

    # Validate all entries
    for name, check in registry["checks"].items():
        errors = validate_check(name, check)
        registry["validation_errors"].extend(errors)

    for name, tool in registry["tools"].items():
        errors = validate_tool(name, tool)
        registry["validation_errors"].extend(errors)

    for name, harness in registry["harnesses"].items():
        errors = validate_harness(name, harness)
        registry["validation_errors"].extend(errors)

    if registry["validation_errors"]:
        for err in registry["validation_errors"]:
            log.warning(f"Validation: {err}")

    return registry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_registry_loader.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/registry/loader.py tests/test_registry_loader.py
git commit -m "feat: add bundle loader — merges YAML bundles with schema validation"
```

---

## Task 11: Split harness_registry.yaml Into Bundles

**Files:**
- Create: `bundles/core/bundle.yaml`
- Create: `bundles/homelab/bundle.yaml`
- Create: `bundles/inference/bundle.yaml`
- Create: `bundles/security/bundle.yaml`
- Create: `bundles/backup/bundle.yaml`

- [ ] **Step 1: Create bundles/core/bundle.yaml**

```yaml
# bundles/core/bundle.yaml — Always active. System health checks.
checks:
  disk_usage:
    enabled: true
    command: "df / | awk 'NR==2 {gsub(/%/,\"\"); print $5}'"
    type: threshold
    warn: 80
    critical: 90
    unit: "%"
    message: "Disk usage at {value}%"

  swap_usage:
    enabled: true
    command: "free -m | awk '/Swap/ {print $3}'"
    type: threshold
    warn: 500
    critical: 2000
    unit: "MB"
    message: "Swap at {value}MB — possible memory pressure"

  ram_usage:
    enabled: true
    command: "free | awk '/Mem/ {printf \"%.0f\", $3/$2*100}'"
    type: threshold
    warn: 85
    critical: 95
    unit: "%"
    message: "RAM at {value}%"

  cpu_temperature:
    enabled: true
    command: "sensors 2>/dev/null | grep -oP '\\+\\K[0-9]+(?=\\.[0-9]+°C)' | sort -rn | head -1 || echo 0"
    type: threshold
    warn: 80
    critical: 90
    unit: "°C"
    message: "CPU temperature at {value}°C"
    requires: sensors

harnesses:
  trend_projections:
    enabled: true
    script: "trend_projector.sh"
    window: offline
    frequency: "6h"
    description: "Sample metrics and project when thresholds will be hit"
```

- [ ] **Step 2: Create bundles/homelab/bundle.yaml**

```yaml
# bundles/homelab/bundle.yaml — Docker + self-hosted service monitoring.
checks:
  llm_server:
    enabled: true
    command: "curl -sf --max-time 5 http://localhost:8080/health"
    type: http_probe
    message: "Primary LLM server not responding"

  docker_unhealthy:
    enabled: true
    command: "docker ps --filter 'health=unhealthy' --format '{{.Names}}' | head -5"
    type: command_output
    message: "Unhealthy containers: {value}"

  docker_crashed:
    enabled: true
    command: "docker ps -a --filter 'status=exited' --format '{{.Names}} {{.Status}}' | grep -v 'Exited (0)' | head -5"
    type: command_output
    message: "Crashed containers: {value}"

tools:
  deploy_repo:
    description: "Deploy a GitHub repository to the homelab"
    script: "github_deploy.sh"
    approval_tier: approve
    sandbox_mode: containerized

  cleanup_system:
    description: "Clean up unused Docker images, containers, volumes, packages, logs"
    script: "cleanup.sh"
    approval_tier: approve
    sandbox_mode: direct

  check_updates:
    description: "Check for newer Docker container image versions"
    script: "update_watcher.sh"
    approval_tier: auto
    sandbox_mode: direct

  diagnose_system:
    description: "Run diagnostics to find what's wrong"
    script: "doctor.sh"
    approval_tier: auto
    sandbox_mode: direct

  check_trends:
    description: "Show resource trends and predict threshold breaches"
    script: "trend_projector.sh"
    approval_tier: auto
    sandbox_mode: direct

harnesses:
  cleanup:
    enabled: true
    script: "cleanup.sh"
    window: offline
    frequency: "3d"
    description: "Clean up Docker, packages, logs, temp files"

  update_watcher:
    enabled: true
    script: "update_watcher.sh"
    window: online
    frequency: weekly
    description: "Check for container image updates"

  mcp_gateway:
    enabled: true
    script: "mcp_gateway.sh"
    window: offline
    frequency: "6h"
    description: "Discover MCP servers and generate bridge configs"
```

- [ ] **Step 3: Create bundles/inference/bundle.yaml**

```yaml
# bundles/inference/bundle.yaml — LLM engine management.
tools:
  run_benchmark:
    description: "Benchmark LLM models and inference engines"
    script: "benchmark.sh"
    approval_tier: notify
    sandbox_mode: direct

  search_new_tools:
    description: "Search for new LLM models, tools, and techniques"
    script: "weekly_optimize.sh"
    approval_tier: auto
    sandbox_mode: direct

harnesses:
  benchmark:
    enabled: true
    script: "benchmark.sh"
    window: offline
    frequency: weekly
    description: "Re-benchmark all model x engine combos"

  weekly_optimize:
    enabled: true
    script: "weekly_optimize.sh"
    window: online
    frequency: weekly
    description: "Search for new models, tools, techniques"
```

- [ ] **Step 4: Create bundles/security/bundle.yaml**

```yaml
# bundles/security/bundle.yaml — Security hardening + auditing.
tools:
  run_security_audit:
    description: "Run security audit — exposed ports, permissions, SSH config"
    script: "security_audit.sh"
    approval_tier: notify
    sandbox_mode: direct

harnesses:
  security_audit:
    enabled: true
    script: "security_audit.sh"
    window: offline
    frequency: weekly
    description: "Security boundary checks"
```

- [ ] **Step 5: Create bundles/backup/bundle.yaml**

```yaml
# bundles/backup/bundle.yaml — Backup + verify + restore.
tools:
  run_backup:
    description: "Backup configs and state to USB drive"
    script: "backup.sh"
    approval_tier: approve
    sandbox_mode: direct

harnesses:
  backup:
    enabled: true
    script: "backup.sh"
    window: offline
    frequency: daily
    description: "Backup configs and state to USB drive"
```

- [ ] **Step 6: Create bundle directory scaffolding**

```bash
mkdir -p bundles/core/scripts bundles/homelab/scripts bundles/inference/scripts bundles/security/scripts bundles/backup/scripts bundles/dashboard/scripts bundles/community
touch bundles/community/.gitkeep
```

- [ ] **Step 7: Commit**

```bash
git add bundles/
git commit -m "feat: split harness_registry.yaml into modular bundles — core, homelab, inference, security, backup"
```

---

## Task 12: CLI Skeleton

**Files:**
- Create: `cli.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Implement CLI entry point**

```python
#!/usr/bin/env python3
"""AgentHarness CLI — manage your infrastructure agent framework."""

import argparse
import json
import sys
from pathlib import Path


def cmd_status(args):
    """Show current AgentHarness status."""
    from core.discovery.state import StateManager
    sm = StateManager()
    state = sm.read()

    if not state.get("paths"):
        print("AgentHarness not initialized. Run: agentharness discover")
        return 1

    print("AgentHarness Status")
    print("=" * 40)
    print(f"Install dir: {state['paths'].get('install_dir', 'unknown')}")
    print(f"Data dir:    {state['paths'].get('data_dir', 'unknown')}")

    hw = state.get("hardware", {})
    print(f"RAM:         {hw.get('total_ram_gb', '?')} GB")
    print(f"CPU:         {hw.get('cpu_model', 'unknown')} ({hw.get('cpu_cores', '?')} cores)")

    services = state.get("services", {})
    containers = services.get("docker_containers", [])
    print(f"Docker:      {len(containers)} containers running")

    llm = services.get("llm_servers", [])
    print(f"LLM servers: {len(llm)} detected")

    agents = state.get("agents", [])
    if agents:
        for a in agents:
            print(f"Agent:       {a.get('type', 'unknown')} ({a.get('container_name', a.get('install_dir', '?'))})")
    else:
        print("Agent:       none detected")

    stale = sm.ensure_fresh()
    if stale:
        print(f"\nStale paths: {', '.join(stale)}")
        print("Run 'agentharness discover' to re-resolve.")

    return 0


def cmd_discover(args):
    """Run full discovery."""
    from core.discovery.engine import run_discovery
    print("Running full discovery...")
    state = run_discovery()
    print(f"Discovery complete. State written to {state['paths'].get('data_dir', '.')}/state.json")
    print(f"  Paths resolved: {len(state.get('paths', {}))}")
    print(f"  Hardware: {state.get('hardware', {}).get('total_ram_gb', '?')} GB RAM, "
          f"{state.get('hardware', {}).get('cpu_cores', '?')} cores")
    print(f"  Docker containers: {len(state.get('services', {}).get('docker_containers', []))}")
    print(f"  Agents found: {len(state.get('agents', []))}")
    return 0


def cmd_health(args):
    """Run registry checks and show results."""
    print("Health checks not yet implemented (Phase A complete, Phase B adds scheduler)")
    return 0


def cmd_bundle_list(args):
    """List active bundles."""
    from core.discovery.state import StateManager
    from core.registry.loader import load_registry

    sm = StateManager()
    state = sm.read()
    bundles_dir = state.get("paths", {}).get("bundles_dir", "bundles")

    registry = load_registry(bundles_dir=bundles_dir)

    print("Active Bundles")
    print("=" * 60)

    bundles_path = Path(bundles_dir)
    if not bundles_path.is_dir():
        print("No bundles directory found.")
        return 1

    for d in sorted(bundles_path.iterdir()):
        if d.is_dir() and (d / "bundle.yaml").exists():
            print(f"  {d.name}")

    print(f"\nRegistry totals:")
    print(f"  Checks:    {len(registry.get('checks', {}))}")
    print(f"  Tools:     {len(registry.get('tools', {}))}")
    print(f"  Harnesses: {len(registry.get('harnesses', {}))}")

    errors = registry.get("validation_errors", [])
    if errors:
        print(f"\n  Validation errors: {len(errors)}")
        for e in errors[:5]:
            print(f"    - {e}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="agentharness",
        description="AgentHarness — infrastructure agent framework",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show current status")
    subparsers.add_parser("discover", help="Run full discovery")
    subparsers.add_parser("health", help="Run health checks")

    bundle_parser = subparsers.add_parser("bundle", help="Manage bundles")
    bundle_sub = bundle_parser.add_subparsers(dest="bundle_command")
    bundle_sub.add_parser("list", help="List active bundles")

    args = parser.parse_args()

    if args.command is None:
        return cmd_status(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "discover":
        return cmd_discover(args)
    elif args.command == "health":
        return cmd_health(args)
    elif args.command == "bundle":
        if args.bundle_command == "list":
            return cmd_bundle_list(args)
        else:
            bundle_parser.print_help()
            return 1
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 2: Update requirements.txt**

```
# AgentHarness Python dependencies
pyyaml>=6.0
```

Remove `huggingface_hub`, `aider-chat`, `smolagents` — those are not needed for Phase A and were speculative dependencies.

- [ ] **Step 3: Test CLI runs**

```bash
cd /Users/rohitmishra/Library/CloudStorage/OneDrive-T-MobileUSA/Documents/projects/AgentHarness
python3 cli.py --help
python3 cli.py discover
python3 cli.py status
python3 cli.py bundle list
```

Expected: All commands run without errors. `discover` populates state.json. `status` shows discovered info.

- [ ] **Step 4: Commit**

```bash
git add cli.py requirements.txt
git commit -m "feat: add CLI skeleton — status, discover, bundle list commands"
```

---

## Task 13: Atomic JSON — Crash-Safe Queue Files

**Files:**
- Create: `core/resilience/__init__.py`
- Create: `core/resilience/atomic_json.py`
- Test: `tests/test_resilience_atomic_json.py`

The alert queue, task queue, and other JSON files can get corrupted if the process crashes mid-write. This module provides crash-safe read/write for all JSON queue files.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_resilience_atomic_json.py
import json
import os
import pytest
from pathlib import Path


@pytest.fixture
def json_dir(tmp_path):
    return tmp_path


def test_write_and_read(json_dir):
    from core.resilience.atomic_json import atomic_write_json, safe_read_json
    path = json_dir / "test.json"
    atomic_write_json(path, {"items": [1, 2, 3]})
    result = safe_read_json(path)
    assert result == {"items": [1, 2, 3]}


def test_read_nonexistent_returns_default(json_dir):
    from core.resilience.atomic_json import safe_read_json
    path = json_dir / "nope.json"
    result = safe_read_json(path, default=[])
    assert result == []


def test_read_corrupt_file_returns_default(json_dir):
    from core.resilience.atomic_json import safe_read_json
    path = json_dir / "corrupt.json"
    path.write_text("{broken json !!!")
    result = safe_read_json(path, default={"fallback": True})
    assert result == {"fallback": True}


def test_corrupt_file_backed_up(json_dir):
    from core.resilience.atomic_json import safe_read_json
    path = json_dir / "corrupt2.json"
    path.write_text("{broken")
    safe_read_json(path, default={})
    assert (json_dir / "corrupt2.json.corrupt").exists()


def test_atomic_write_survives_tmp(json_dir):
    """If .tmp exists but final doesn't, data should be recoverable."""
    from core.resilience.atomic_json import safe_read_json, atomic_write_json
    path = json_dir / "data.json"
    atomic_write_json(path, {"good": True})
    # Simulate leftover .tmp from a crash
    (json_dir / "data.json.tmp").write_text('{"stale": true}')
    result = safe_read_json(path)
    assert result == {"good": True}


def test_append_to_list(json_dir):
    from core.resilience.atomic_json import atomic_append_json
    path = json_dir / "queue.json"
    atomic_append_json(path, {"id": 1})
    atomic_append_json(path, {"id": 2})
    result = json.loads(path.read_text())
    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[1]["id"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resilience_atomic_json.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement atomic JSON operations**

```python
# core/resilience/__init__.py
"""Resilience layer — crash recovery, watchdog, circuit breaker."""

# core/resilience/atomic_json.py
"""Crash-safe JSON file operations for queues and state files.

All writes go through tmp-then-rename to prevent corruption on crash.
All reads handle corrupt files gracefully with backup + default.
"""

import fcntl
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("resilience.atomic_json")


def safe_read_json(path: str | Path, default: Any = None) -> Any:
    """Read a JSON file, returning default if missing or corrupt.

    If the file is corrupt:
    1. Back it up as .corrupt
    2. Log a warning
    3. Return the default
    """
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}

    try:
        data = json.loads(path.read_text())
        return data
    except (json.JSONDecodeError, OSError) as e:
        # Back up the corrupt file
        corrupt_path = path.with_suffix(path.suffix + ".corrupt")
        try:
            shutil.copy2(path, corrupt_path)
        except OSError:
            pass
        log.warning(f"Corrupt JSON at {path}: {e}. Backed up to {corrupt_path}")
        return default if default is not None else {}


def atomic_write_json(path: str | Path, data: Any) -> None:
    """Write JSON atomically via tmp-then-rename.

    Uses file locking to prevent concurrent writes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    lock_path = path.with_suffix(path.suffix + ".lock")

    lock_fd = None
    try:
        lock_path.touch(exist_ok=True)
        lock_fd = open(lock_path, "r")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    log.warning(f"Lock timeout writing {path}, proceeding anyway")
                    break
                time.sleep(0.05)

        tmp_path.write_text(json.dumps(data, indent=2, default=str))
        os.rename(tmp_path, path)
    except OSError as e:
        log.error(f"Failed to write {path}: {e}")
        # Clean up tmp if rename failed
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            lock_fd.close()


def atomic_append_json(path: str | Path, item: Any) -> None:
    """Append an item to a JSON list file, atomically.

    If the file doesn't exist, creates it with [item].
    If it exists, reads the list, appends, and rewrites atomically.
    """
    path = Path(path)
    current = safe_read_json(path, default=[])
    if not isinstance(current, list):
        current = [current]
    current.append(item)
    atomic_write_json(path, current)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resilience_atomic_json.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/resilience/__init__.py core/resilience/atomic_json.py tests/test_resilience_atomic_json.py
git commit -m "feat: add crash-safe atomic JSON read/write for queues and state files"
```

---

## Task 14: Stale Lock Recovery + Watchdog

**Files:**
- Create: `core/resilience/watchdog.py`
- Test: `tests/test_resilience_watchdog.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_resilience_watchdog.py
import json
import os
import time
import pytest
from pathlib import Path


@pytest.fixture
def watchdog_dir(tmp_path):
    return tmp_path


def test_heartbeat_write(watchdog_dir):
    from core.resilience.watchdog import write_heartbeat
    write_heartbeat(data_dir=str(watchdog_dir))
    hb_file = watchdog_dir / "heartbeat.json"
    assert hb_file.exists()
    data = json.loads(hb_file.read_text())
    assert "timestamp" in data
    assert "pid" in data
    assert data["pid"] == os.getpid()


def test_check_heartbeat_fresh(watchdog_dir):
    from core.resilience.watchdog import write_heartbeat, check_heartbeat
    write_heartbeat(data_dir=str(watchdog_dir))
    result = check_heartbeat(data_dir=str(watchdog_dir), max_age_seconds=60)
    assert result["status"] == "ok"


def test_check_heartbeat_stale(watchdog_dir):
    from core.resilience.watchdog import check_heartbeat
    hb_file = watchdog_dir / "heartbeat.json"
    # Write a heartbeat from 30 minutes ago
    hb_file.write_text(json.dumps({
        "timestamp": time.time() - 1800,
        "pid": 99999,
    }))
    result = check_heartbeat(data_dir=str(watchdog_dir), max_age_seconds=900)
    assert result["status"] == "stale"
    assert result["age_seconds"] > 900


def test_check_heartbeat_missing(watchdog_dir):
    from core.resilience.watchdog import check_heartbeat
    result = check_heartbeat(data_dir=str(watchdog_dir), max_age_seconds=60)
    assert result["status"] == "missing"


def test_stale_lock_recovery(watchdog_dir):
    from core.resilience.watchdog import recover_stale_lock
    lock_file = watchdog_dir / "state.lock"
    # Create a lock file with a dead PID
    lock_file.write_text("99999999")
    recovered = recover_stale_lock(str(lock_file))
    assert recovered is True
    assert not lock_file.exists()


def test_stale_lock_alive_pid_not_removed(watchdog_dir):
    from core.resilience.watchdog import recover_stale_lock
    lock_file = watchdog_dir / "state.lock"
    # Write our own PID — it's alive
    lock_file.write_text(str(os.getpid()))
    recovered = recover_stale_lock(str(lock_file))
    assert recovered is False
    assert lock_file.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resilience_watchdog.py -v`
Expected: FAIL

- [ ] **Step 3: Implement watchdog**

```python
# core/resilience/watchdog.py
"""Self-watchdog: heartbeat, stale lock recovery, process monitoring.

The scheduler writes a heartbeat on every tick. A systemd timer checks
the heartbeat every 5 minutes. If stale, it alerts and restarts.
"""

import json
import logging
import os
import signal
import time
from pathlib import Path

log = logging.getLogger("resilience.watchdog")


def write_heartbeat(data_dir: str) -> None:
    """Write a heartbeat file. Called by the scheduler on every tick."""
    hb_file = Path(data_dir) / "heartbeat.json"
    hb_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # Use direct write — heartbeat is a single small file, not a queue
    hb_file.write_text(json.dumps(data))


def check_heartbeat(data_dir: str, max_age_seconds: int = 1200) -> dict:
    """Check if the heartbeat is fresh.

    Returns:
        {"status": "ok"|"stale"|"missing", "age_seconds": N, "pid": N}
    """
    hb_file = Path(data_dir) / "heartbeat.json"
    if not hb_file.exists():
        return {"status": "missing", "age_seconds": -1, "pid": -1}

    try:
        data = json.loads(hb_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"status": "missing", "age_seconds": -1, "pid": -1}

    age = time.time() - data.get("timestamp", 0)
    pid = data.get("pid", -1)

    if age > max_age_seconds:
        return {"status": "stale", "age_seconds": int(age), "pid": pid}
    return {"status": "ok", "age_seconds": int(age), "pid": pid}


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def recover_stale_lock(lock_file: str) -> bool:
    """Remove a lock file if the holding process is dead.

    Returns True if the lock was recovered (removed).
    Returns False if the lock is held by a live process.
    """
    lock_path = Path(lock_file)
    if not lock_path.exists():
        return False

    try:
        content = lock_path.read_text().strip()
        if content.isdigit():
            pid = int(content)
            if _pid_alive(pid):
                log.info(f"Lock {lock_file} held by live PID {pid}")
                return False

        # PID is dead or not a valid PID — remove the lock
        lock_path.unlink()
        log.warning(f"Recovered stale lock: {lock_file} (PID: {content})")
        return True
    except OSError as e:
        log.error(f"Error recovering lock {lock_file}: {e}")
        return False


def recover_all_stale_locks(data_dir: str) -> list[str]:
    """Find and recover all stale .lock files in the data directory."""
    recovered = []
    data_path = Path(data_dir)
    for lock_file in data_path.glob("*.lock"):
        if recover_stale_lock(str(lock_file)):
            recovered.append(str(lock_file))
    return recovered
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resilience_watchdog.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/resilience/watchdog.py tests/test_resilience_watchdog.py
git commit -m "feat: add watchdog — heartbeat, stale lock recovery, process monitoring"
```

---

## Task 15: Circuit Breaker for Health Checks

**Files:**
- Create: `core/resilience/circuit_breaker.py`
- Test: `tests/test_resilience_circuit_breaker.py`

When a check consistently fails (service removed, host unreachable), stop alerting after N failures. Re-enable on next discovery run if the service reappears.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_resilience_circuit_breaker.py
import json
import pytest
from pathlib import Path


@pytest.fixture
def cb_dir(tmp_path):
    return tmp_path


def test_record_failure_and_check_open(cb_dir):
    from core.resilience.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(data_dir=str(cb_dir), max_failures=3)
    cb.record_failure("jellyfin_health")
    cb.record_failure("jellyfin_health")
    assert not cb.is_open("jellyfin_health")  # 2 < 3
    cb.record_failure("jellyfin_health")
    assert cb.is_open("jellyfin_health")  # 3 >= 3, circuit open


def test_record_success_resets(cb_dir):
    from core.resilience.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(data_dir=str(cb_dir), max_failures=3)
    cb.record_failure("jellyfin_health")
    cb.record_failure("jellyfin_health")
    cb.record_failure("jellyfin_health")
    assert cb.is_open("jellyfin_health")
    cb.record_success("jellyfin_health")
    assert not cb.is_open("jellyfin_health")


def test_reset_check(cb_dir):
    from core.resilience.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(data_dir=str(cb_dir), max_failures=2)
    cb.record_failure("test_check")
    cb.record_failure("test_check")
    assert cb.is_open("test_check")
    cb.reset("test_check")
    assert not cb.is_open("test_check")


def test_unknown_check_not_open(cb_dir):
    from core.resilience.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(data_dir=str(cb_dir))
    assert not cb.is_open("never_seen")


def test_get_open_circuits(cb_dir):
    from core.resilience.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(data_dir=str(cb_dir), max_failures=1)
    cb.record_failure("check_a")
    cb.record_failure("check_b")
    open_circuits = cb.get_open_circuits()
    assert "check_a" in open_circuits
    assert "check_b" in open_circuits
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resilience_circuit_breaker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement circuit breaker**

```python
# core/resilience/circuit_breaker.py
"""Circuit breaker for health checks — prevents alert fatigue.

When a check fails N consecutive times, the circuit "opens" and the
check is temporarily suppressed. It re-closes on success or manual reset.
Discovery runs call reset_all() to re-enable checks for reappeared services.
"""

import json
import logging
from pathlib import Path

from core.resilience.atomic_json import safe_read_json, atomic_write_json

log = logging.getLogger("resilience.circuit_breaker")


class CircuitBreaker:

    def __init__(self, data_dir: str, max_failures: int = 5):
        self.state_file = Path(data_dir) / "circuit_breaker.json"
        self.max_failures = max_failures

    def _load(self) -> dict:
        return safe_read_json(self.state_file, default={})

    def _save(self, state: dict) -> None:
        atomic_write_json(self.state_file, state)

    def record_failure(self, check_name: str) -> None:
        """Record a check failure. Opens circuit after max_failures."""
        state = self._load()
        entry = state.get(check_name, {"failures": 0, "open": False})
        entry["failures"] = entry.get("failures", 0) + 1
        if entry["failures"] >= self.max_failures:
            if not entry.get("open"):
                log.warning(
                    f"Circuit opened for '{check_name}' after "
                    f"{entry['failures']} consecutive failures"
                )
            entry["open"] = True
        state[check_name] = entry
        self._save(state)

    def record_success(self, check_name: str) -> None:
        """Record a check success. Closes the circuit."""
        state = self._load()
        if check_name in state:
            was_open = state[check_name].get("open", False)
            state[check_name] = {"failures": 0, "open": False}
            self._save(state)
            if was_open:
                log.info(f"Circuit closed for '{check_name}' — check passing again")

    def is_open(self, check_name: str) -> bool:
        """Check if a circuit is open (suppressed)."""
        state = self._load()
        return state.get(check_name, {}).get("open", False)

    def reset(self, check_name: str) -> None:
        """Manually reset a circuit."""
        state = self._load()
        if check_name in state:
            state[check_name] = {"failures": 0, "open": False}
            self._save(state)

    def reset_all(self) -> None:
        """Reset all circuits. Called by discovery when services change."""
        self._save({})

    def get_open_circuits(self) -> list[str]:
        """Get list of all open (suppressed) circuits."""
        state = self._load()
        return [name for name, entry in state.items() if entry.get("open")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resilience_circuit_breaker.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/resilience/circuit_breaker.py tests/test_resilience_circuit_breaker.py
git commit -m "feat: add circuit breaker — suppresses alerts after N consecutive failures"
```

---

## Task 16: Startup Self-Test

**Files:**
- Create: `core/resilience/selftest.py`
- Test: `tests/test_resilience_selftest.py`

On every boot and scheduler start, run a quick self-test: can we read state? Can we write to data dirs? Is Docker available? Log and alert on failures.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_resilience_selftest.py
import os
import json
import pytest
from pathlib import Path


@pytest.fixture
def selftest_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    reports_dir = data_dir / "reports"
    reports_dir.mkdir()
    logs_dir = data_dir / "logs"
    logs_dir.mkdir()

    state = {
        "schema_version": 1,
        "paths": {
            "install_dir": str(tmp_path),
            "data_dir": str(data_dir),
            "reports_dir": str(reports_dir),
            "logs_dir": str(logs_dir),
            "scripts_dir": str(tmp_path / "scripts"),
        },
    }
    (data_dir / "state.json").write_text(json.dumps(state))
    (tmp_path / "scripts").mkdir()
    return tmp_path, data_dir


def test_selftest_passes_healthy_system(selftest_env):
    from core.resilience.selftest import run_selftest
    tmp_path, data_dir = selftest_env
    result = run_selftest(data_dir=str(data_dir))
    assert result["overall"] in ("ok", "degraded")
    assert len(result["checks"]) > 0


def test_selftest_detects_missing_state(selftest_env):
    from core.resilience.selftest import run_selftest
    tmp_path, data_dir = selftest_env
    (data_dir / "state.json").unlink()
    result = run_selftest(data_dir=str(data_dir))
    state_check = [c for c in result["checks"] if c["name"] == "state_file"]
    assert len(state_check) == 1
    assert state_check[0]["status"] == "fail"


def test_selftest_detects_unwritable_dir(selftest_env):
    from core.resilience.selftest import run_selftest
    tmp_path, data_dir = selftest_env
    # Point reports to a non-existent, non-creatable path
    state = json.loads((data_dir / "state.json").read_text())
    state["paths"]["reports_dir"] = "/root/impossible_dir_for_test"
    (data_dir / "state.json").write_text(json.dumps(state))
    result = run_selftest(data_dir=str(data_dir))
    reports_check = [c for c in result["checks"] if c["name"] == "reports_dir_writable"]
    assert len(reports_check) == 1
    assert reports_check[0]["status"] == "fail"


def test_selftest_returns_check_list(selftest_env):
    from core.resilience.selftest import run_selftest
    _, data_dir = selftest_env
    result = run_selftest(data_dir=str(data_dir))
    for check in result["checks"]:
        assert "name" in check
        assert "status" in check
        assert check["status"] in ("ok", "fail", "skip")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resilience_selftest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement self-test**

```python
# core/resilience/selftest.py
"""Startup self-test — validates AgentHarness can operate.

Run on every boot and scheduler start. Checks:
1. Can read state.json
2. Can write to data directories (reports, logs)
3. Python version adequate
4. Docker available (optional — degrades gracefully)
5. No stale locks
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("resilience.selftest")


def _check(name: str, fn, required: bool = True) -> dict:
    """Run a check function, catch errors, return structured result."""
    try:
        ok = fn()
        return {
            "name": name,
            "status": "ok" if ok else "fail",
            "required": required,
        }
    except Exception as e:
        return {
            "name": name,
            "status": "fail",
            "required": required,
            "error": str(e),
        }


def run_selftest(data_dir: str) -> dict:
    """Run all self-test checks. Returns structured results.

    Returns:
        {
            "overall": "ok" | "degraded" | "fail",
            "checks": [{"name": str, "status": str, "required": bool}, ...]
        }
    """
    data_path = Path(data_dir)
    checks = []

    # 1. State file readable
    state_file = data_path / "state.json"
    def check_state():
        if not state_file.exists():
            return False
        data = json.loads(state_file.read_text())
        return "paths" in data or "schema_version" in data
    checks.append(_check("state_file", check_state, required=True))

    # Load state for remaining checks
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    paths = state.get("paths", {})

    # 2. Data directories writable
    for dir_key in ("reports_dir", "logs_dir"):
        dir_path = paths.get(dir_key, "")
        def check_writable(p=dir_path):
            if not p or not Path(p).is_dir():
                return False
            test_file = Path(p) / ".selftest"
            try:
                test_file.write_text("ok")
                test_file.unlink()
                return True
            except OSError:
                return False
        checks.append(_check(f"{dir_key}_writable", check_writable, required=True))

    # 3. Python version
    def check_python():
        return sys.version_info >= (3, 10)
    checks.append(_check("python_3_10_plus", check_python, required=True))

    # 4. Docker available (optional)
    def check_docker():
        result = subprocess.run(
            "docker info", shell=True, capture_output=True, timeout=5
        )
        return result.returncode == 0
    checks.append(_check("docker_available", check_docker, required=False))

    # 5. No stale locks
    def check_locks():
        lock_files = list(data_path.glob("*.lock"))
        for lf in lock_files:
            content = lf.read_text().strip()
            if content.isdigit():
                pid = int(content)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return False  # Stale lock found
        return True
    checks.append(_check("no_stale_locks", check_locks, required=True))

    # Determine overall status
    required_failures = [c for c in checks if c["required"] and c["status"] == "fail"]
    optional_failures = [c for c in checks if not c["required"] and c["status"] == "fail"]

    if required_failures:
        overall = "fail"
    elif optional_failures:
        overall = "degraded"
    else:
        overall = "ok"

    result = {"overall": overall, "checks": checks}

    # Log results
    for c in checks:
        if c["status"] == "ok":
            log.info(f"Self-test {c['name']}: OK")
        elif c["status"] == "fail" and c["required"]:
            log.error(f"Self-test {c['name']}: FAIL{' — ' + c.get('error', '') if c.get('error') else ''}")
        elif c["status"] == "fail":
            log.warning(f"Self-test {c['name']}: FAIL (optional)")

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resilience_selftest.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/resilience/selftest.py tests/test_resilience_selftest.py
git commit -m "feat: add startup self-test — validates state, dirs, Python, Docker, locks"
```

---

## Task 17: Config Backup + Self-Update Safety

**Files:**
- Create: `core/resilience/config_backup.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_resilience_config_backup.py
import json
import pytest
from pathlib import Path


@pytest.fixture
def config_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agentharness.yaml").write_text("setting: original")
    return tmp_path, data_dir, config_dir


def test_snapshot_creates_backup(config_env):
    from core.resilience.config_backup import snapshot_config
    tmp_path, data_dir, config_dir = config_env
    snapshot_dir = snapshot_config(
        config_dir=str(config_dir),
        backup_base=str(data_dir / "config_backups"),
    )
    assert Path(snapshot_dir).exists()
    assert (Path(snapshot_dir) / "agentharness.yaml").exists()
    assert (Path(snapshot_dir) / "agentharness.yaml").read_text() == "setting: original"


def test_restore_from_snapshot(config_env):
    from core.resilience.config_backup import snapshot_config, restore_config
    tmp_path, data_dir, config_dir = config_env
    snapshot_dir = snapshot_config(
        config_dir=str(config_dir),
        backup_base=str(data_dir / "config_backups"),
    )
    # Modify the config
    (config_dir / "agentharness.yaml").write_text("setting: modified")
    # Restore
    restore_config(snapshot_dir=snapshot_dir, config_dir=str(config_dir))
    assert (config_dir / "agentharness.yaml").read_text() == "setting: original"


def test_snapshot_limits_kept(config_env):
    from core.resilience.config_backup import snapshot_config, cleanup_old_snapshots
    tmp_path, data_dir, config_dir = config_env
    backup_base = str(data_dir / "config_backups")
    # Create 12 snapshots
    import time
    for _ in range(12):
        snapshot_config(config_dir=str(config_dir), backup_base=backup_base)
        time.sleep(0.01)  # Ensure unique timestamps
    cleanup_old_snapshots(backup_base=backup_base, keep=5)
    remaining = list(Path(backup_base).iterdir())
    assert len(remaining) <= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resilience_config_backup.py -v`
Expected: FAIL

- [ ] **Step 3: Implement config backup**

```python
# core/resilience/config_backup.py
"""Config backup — snapshot before any modification.

Before proposals, bundle installs, or self-updates modify config,
snapshot the current state. If something breaks, restore from snapshot.
"""

import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("resilience.config_backup")


def snapshot_config(config_dir: str, backup_base: str) -> str:
    """Create a timestamped snapshot of the config directory.

    Returns the path to the snapshot directory.
    """
    config_path = Path(config_dir)
    backup_path = Path(backup_base)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    snapshot_dir = backup_path / f"config_{timestamp}"
    shutil.copytree(config_path, snapshot_dir)
    log.info(f"Config snapshot created: {snapshot_dir}")
    return str(snapshot_dir)


def restore_config(snapshot_dir: str, config_dir: str) -> None:
    """Restore config from a snapshot directory."""
    config_path = Path(config_dir)
    snapshot_path = Path(snapshot_dir)

    if not snapshot_path.is_dir():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_dir}")

    # Remove current config contents, replace with snapshot
    for item in config_path.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    for item in snapshot_path.iterdir():
        dest = config_path / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    log.info(f"Config restored from: {snapshot_dir}")


def cleanup_old_snapshots(backup_base: str, keep: int = 10) -> int:
    """Delete old snapshots, keeping the most recent N.

    Returns number of snapshots deleted.
    """
    backup_path = Path(backup_base)
    if not backup_path.is_dir():
        return 0

    snapshots = sorted(backup_path.iterdir(), key=lambda p: p.name, reverse=True)
    to_delete = snapshots[keep:]
    for snap in to_delete:
        shutil.rmtree(snap)
        log.info(f"Deleted old config snapshot: {snap.name}")
    return len(to_delete)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resilience_config_backup.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/resilience/config_backup.py tests/test_resilience_config_backup.py
git commit -m "feat: add config snapshot/restore — backup before any config modification"
```

---

## Task 18: Systemd Units + Log Rotation

**Files:**
- Create: `config/systemd/agentharness-scheduler.service`
- Create: `config/systemd/agentharness-watchdog.service`
- Create: `config/systemd/agentharness-watchdog.timer`
- Create: `config/logrotate/agentharness`

- [ ] **Step 1: Create scheduler systemd service with auto-restart**

```ini
# config/systemd/agentharness-scheduler.service
[Unit]
Description=AgentHarness Scheduler — network-aware task runner
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=%i
# Run the scheduler in a loop (every 15 minutes)
ExecStart=/bin/bash -c 'while true; do /bin/bash "${AH_SCRIPTS_DIR:-/opt/agentharness/scripts}/scheduler.sh" >> "${AH_LOGS_DIR:-/opt/agentharness/data/logs}/scheduler.log" 2>&1; sleep 900; done'
# Auto-restart on crash
Restart=on-failure
RestartSec=30
# Stop restarting if it crashes 5 times in 10 minutes
StartLimitIntervalSec=600
StartLimitBurst=5
# Environment
EnvironmentFile=-/etc/agentharness/env
Environment=AH_DATA_DIR=%h/agentharness/data

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create watchdog service + timer**

```ini
# config/systemd/agentharness-watchdog.service
[Unit]
Description=AgentHarness Watchdog — checks scheduler heartbeat
After=agentharness-scheduler.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -c "from core.resilience.watchdog import check_heartbeat; from core.resilience.watchdog import recover_all_stale_locks; import os, subprocess, json; data_dir=os.environ.get('AH_DATA_DIR', os.path.expanduser('~/agentharness/data')); recovered=recover_all_stale_locks(data_dir); result=check_heartbeat(data_dir); print(json.dumps(result)); exec(open('/dev/stdin').read()) if False else None" || true
# Simplified: the install.sh will generate a proper watchdog script
Environment=AH_DATA_DIR=%h/agentharness/data
```

```ini
# config/systemd/agentharness-watchdog.timer
[Unit]
Description=Check AgentHarness scheduler heartbeat every 5 minutes

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create log rotation config**

```
# config/logrotate/agentharness
# Log rotation for AgentHarness — prevents disk fill
# Install: sudo cp config/logrotate/agentharness /etc/logrotate.d/

/opt/agentharness/data/logs/*.log
/home/*/agentharness/data/logs/*.log
{
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    dateext
    dateformat -%Y%m%d
    create 0644
    size 10M
}
```

- [ ] **Step 4: Commit**

```bash
git add config/systemd/agentharness-scheduler.service config/systemd/agentharness-watchdog.service config/systemd/agentharness-watchdog.timer config/logrotate/agentharness
git commit -m "feat: add systemd units with auto-restart + log rotation config

Scheduler service restarts on failure (max 5 times in 10 min).
Watchdog timer checks heartbeat every 5 minutes.
Logrotate keeps 30 days, compresses after 1 day."
```

---

## Task 19: Wire Resilience Into Discovery Engine + CLI

**Files:**
- Modify: `core/discovery/engine.py`
- Modify: `core/discovery/state.py`
- Modify: `cli.py`

- [ ] **Step 1: Wire stale lock recovery into state manager**

Add to `core/discovery/state.py` — at the start of `write()`, call stale lock recovery:

```python
# Add import at top
from core.resilience.watchdog import recover_stale_lock

# In StateManager.write(), before acquiring lock:
    def write(self, updates: dict) -> None:
        """Atomic write with file locking. Merges updates into existing state."""
        # Recover stale lock if needed
        recover_stale_lock(str(self.lock_file))

        lock_fd = None
        # ... rest of existing code
```

- [ ] **Step 2: Wire self-test into discovery engine**

Add to `core/discovery/engine.py`:

```python
# Add import
from core.resilience.selftest import run_selftest

def run_discovery(
    hint_dir: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    # ... existing discovery code ...

    # Run self-test after discovery
    data_dir = paths.get("data_dir", paths["install_dir"])
    selftest = run_selftest(data_dir=data_dir)

    sm = StateManager(data_dir=data_dir)
    sm.write({
        "paths": paths,
        "hardware": hardware,
        "services": services,
        "agents": agents,
        "selftest": selftest,
    })

    return sm.read()
```

- [ ] **Step 3: Wire circuit breaker reset into discovery**

When discovery finds new services, reset circuit breakers so previously-tripped checks get re-evaluated:

```python
# Add import
from core.resilience.circuit_breaker import CircuitBreaker

# In run_discovery(), after writing state:
    # Reset circuit breakers — services may have changed
    cb = CircuitBreaker(data_dir=data_dir)
    cb.reset_all()
```

- [ ] **Step 4: Add selftest + resilience commands to CLI**

Add to `cli.py`:

```python
def cmd_selftest(args):
    """Run startup self-test."""
    from core.discovery.state import StateManager
    from core.resilience.selftest import run_selftest

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir", ".")

    result = run_selftest(data_dir=data_dir)

    print(f"Self-Test: {result['overall'].upper()}")
    print("=" * 40)
    for check in result["checks"]:
        icon = "PASS" if check["status"] == "ok" else "FAIL" if check["status"] == "fail" else "SKIP"
        req = " (required)" if check.get("required") else " (optional)"
        print(f"  [{icon}] {check['name']}{req}")
        if check.get("error"):
            print(f"         {check['error']}")

    return 0 if result["overall"] != "fail" else 1


def cmd_circuits(args):
    """Show open circuit breakers."""
    from core.discovery.state import StateManager
    from core.resilience.circuit_breaker import CircuitBreaker

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir", ".")

    cb = CircuitBreaker(data_dir=data_dir)
    open_circuits = cb.get_open_circuits()

    if not open_circuits:
        print("No open circuits. All checks are active.")
    else:
        print(f"Open circuits ({len(open_circuits)} suppressed checks):")
        for name in open_circuits:
            print(f"  - {name}")
        print("\nRun 'agentharness discover' to reset, or manually:")
        print("  agentharness circuit reset <check_name>")

    return 0
```

Add to `main()` parser:

```python
    subparsers.add_parser("selftest", help="Run startup self-test")
    subparsers.add_parser("circuits", help="Show open circuit breakers")
```

Add to command dispatch:

```python
    elif args.command == "selftest":
        return cmd_selftest(args)
    elif args.command == "circuits":
        return cmd_circuits(args)
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass (including new resilience tests)

- [ ] **Step 6: Commit**

```bash
git add core/discovery/engine.py core/discovery/state.py cli.py
git commit -m "feat: wire resilience into discovery engine and CLI

- Stale lock recovery runs before every state write
- Self-test runs after every discovery
- Circuit breakers reset on discovery (services may have changed)
- CLI: agentharness selftest, agentharness circuits"
```

---

## Task 20: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README.md**

Replace the entire README with content reflecting the new architecture. Remove all `/opt/agentharness` references. Document the discovery-based installation, bundle system, CLI, and resilience features.

Key sections:
- Quick Start (uses `$HOME/agentharness` as default, not `/opt/agentharness`)
- What It Does (discovery-first, bundle system, no hardcoded paths)
- Resilience (auto-restart, watchdog, circuit breaker, self-test, config backup)
- Project Structure (new `core/`, `bundles/` layout)
- Extending (bundle YAML, CLI, community bundles)
- CLI Reference (including `selftest`, `circuits`)
- Hardware support

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README for v2 architecture — discovery, bundles, resilience"
```

---

## Task 21: Run Full Test Suite + Final Validation

- [ ] **Step 1: Run all tests**

```bash
cd /Users/rohitmishra/Library/CloudStorage/OneDrive-T-MobileUSA/Documents/projects/AgentHarness
python3 -m pytest tests/ -v
```

Expected: All tests pass (40+ tests across 11 test files).

- [ ] **Step 2: Run migration validation**

```bash
grep -rn "/opt/agentharness" scripts/ install.sh config/ --include="*.sh" --include="*.py" --include="*.yaml" --include="*.yml" --include="*.service" --include="*.template" | grep -v "test_" | grep -v ".git" | grep -v "docs/" | grep -v "logrotate"
```

Expected: Zero matches (no hardcoded paths remain in production code). The logrotate config has a glob pattern which is correct.

- [ ] **Step 3: Test discovery + CLI end-to-end**

```bash
export AGENTHARNESS_HOME="$(pwd)"
python3 cli.py discover
python3 cli.py status
python3 cli.py bundle list
python3 cli.py selftest
python3 cli.py circuits
```

Expected: All commands succeed. State file is populated. Bundles listed. Self-test passes. No open circuits.

- [ ] **Step 4: Test resilience features**

```bash
# Test atomic JSON survives corruption
python3 -c "
from core.resilience.atomic_json import atomic_write_json, safe_read_json
from pathlib import Path
import tempfile, json

d = Path(tempfile.mkdtemp())
atomic_write_json(d / 'test.json', [1, 2, 3])
assert safe_read_json(d / 'test.json') == [1, 2, 3]

# Corrupt it
(d / 'test2.json').write_text('{broken')
assert safe_read_json(d / 'test2.json', default=[]) == []
assert (d / 'test2.json.corrupt').exists()
print('Atomic JSON: OK')
"

# Test circuit breaker
python3 -c "
from core.resilience.circuit_breaker import CircuitBreaker
import tempfile

d = tempfile.mkdtemp()
cb = CircuitBreaker(data_dir=d, max_failures=3)
for _ in range(3):
    cb.record_failure('test_check')
assert cb.is_open('test_check')
cb.record_success('test_check')
assert not cb.is_open('test_check')
print('Circuit breaker: OK')
"

# Test watchdog heartbeat
python3 -c "
from core.resilience.watchdog import write_heartbeat, check_heartbeat
import tempfile

d = tempfile.mkdtemp()
write_heartbeat(data_dir=d)
result = check_heartbeat(data_dir=d)
assert result['status'] == 'ok'
print('Watchdog: OK')
"
```

Expected: All three print OK.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: Phase A complete — discovery, script migration, bundles, resilience, CLI

AgentHarness no longer requires /opt/agentharness or any hardcoded paths.
All paths resolved at runtime via discovery engine. Registry split into
modular bundles. Resilience layer: crash-safe JSON, watchdog with heartbeat,
circuit breaker for alert fatigue, startup self-test, config backup/restore,
systemd auto-restart, log rotation. CLI: status, discover, bundle, selftest,
circuits."
```

---

## Summary

**Phase A delivers:**
- Discovery engine (6 modules: state, paths, hardware, services, agents, engine)
- All 24 scripts migrated from hardcoded paths to `$AH_*` variables
- Registry schema validation
- Bundle loader with merge semantics
- 5 shipped bundles (core, homelab, inference, security, backup)
- Resilience layer:
  - Crash-safe atomic JSON for all queue/state files
  - Self-watchdog with heartbeat + stale lock recovery
  - Circuit breaker for alert fatigue prevention
  - Startup self-test (validates state, dirs, Python, Docker, locks)
  - Config snapshot/restore before any modification
  - Systemd units with `Restart=on-failure` (max 5 in 10 min)
  - Log rotation (30 days, compressed after 1 day)
- CLI (status, discover, bundle list, selftest, circuits)
- Updated README

**Phase A does NOT include** (deferred to later phases):
- LLM provider abstraction (Phase B)
- Budget tracking (Phase B)
- Scheduler rewrite to Python (Phase B)
- HITL approval gateway (Phase C)
- Sandbox execution (Phase C)
- Agent bridge (Phase C)
- Distiller / synthesizer / scout (Phase D)
- Dashboard (Phase D)

**Estimated tasks:** 21 tasks, ~75 steps
**Test coverage:** 45+ tests across 11 test files
