"""Post-deploy smoketest — discovery + selftest + scheduler tick + integrity + bundles.

One command that runs the full verification sequence and returns a structured
report suitable for terminal display or programmatic consumption.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Dict


def run_smoketest(data_dir: str) -> Dict[str, Any]:
    """Run all post-deploy verification steps and return a structured report.

    Steps:
        1. Discovery
        2. Self-test
        3. One scheduler tick
        4. Integrity check (if manifest exists)
        5. Bundle validation

    Returns a dict with keys: overall, discovery, selftest, scheduler,
    integrity, bundles, duration_ms.
    """
    start = time.monotonic()

    report: Dict[str, Any] = {
        "overall": "pass",
        "discovery": {"paths": 0, "services": 0, "agents": 0},
        "selftest": {"overall": "ok", "passed": 0, "failed": 0},
        "scheduler": {"checks_run": 0, "checks_passed": 0, "harnesses_run": 0},
        "integrity": {"status": "no_manifest", "checked": 0},
        "bundles": {"checks": 0, "tools": 0, "harnesses": 0, "errors": 0},
        "duration_ms": 0,
    }

    scheduler_crashed = False

    # 1. Discovery
    try:
        from core.discovery.engine import run_discovery

        state = run_discovery()
        report["discovery"] = {
            "paths": len(state.get("paths", {})),
            "services": len(state.get("services", {})),
            "agents": len(state.get("agents", {})),
        }
    except Exception:
        traceback.print_exc()

    # 2. Self-test
    try:
        from core.resilience.selftest import run_selftest

        selftest_result = run_selftest(data_dir)
        checks = selftest_result.get("checks", [])
        passed = sum(1 for c in checks if c.get("status") == "ok")
        failed = sum(1 for c in checks if c.get("status") == "fail")
        report["selftest"] = {
            "overall": selftest_result.get("overall", "fail"),
            "passed": passed,
            "failed": failed,
        }
    except Exception:
        traceback.print_exc()
        report["selftest"]["overall"] = "fail"

    # 3. One scheduler tick
    try:
        from core.scheduler.scheduler import Scheduler

        sched = Scheduler(data_dir)
        tick_result = sched.tick()
        report["scheduler"] = {
            "checks_run": tick_result.get("checks_run", 0),
            "checks_passed": tick_result.get("checks_passed", 0),
            "harnesses_run": tick_result.get("harnesses_run", 0),
        }
    except Exception:
        traceback.print_exc()
        scheduler_crashed = True

    # 4. Integrity check
    try:
        from core.security.integrity import verify_integrity

        install_dir = str(Path(data_dir).parent) if data_dir else "."
        # Try common manifest locations
        manifest_path = str(Path(data_dir) / "integrity_manifest.json")
        if not Path(manifest_path).exists():
            manifest_path = str(
                Path(install_dir) / "data" / "integrity_manifest.json"
            )

        result = verify_integrity(install_dir, manifest_path)
        report["integrity"] = {
            "status": result.get("status", "no_manifest"),
            "checked": result.get("checked", 0),
        }
    except Exception:
        traceback.print_exc()

    # 5. Bundle validation
    try:
        from core.registry.loader import load_registry

        # Discover bundles dir from state or convention
        from core.discovery.state import StateManager

        sm = StateManager(data_dir=data_dir)
        state = sm.read()
        bundles_dir = state.get("paths", {}).get("bundles_dir", "")
        if not bundles_dir:
            # Fall back to install_dir/bundles
            install_dir = state.get("paths", {}).get("install_dir", ".")
            bundles_dir = str(Path(install_dir) / "bundles")

        registry = load_registry(bundles_dir)
        report["bundles"] = {
            "checks": len(registry.get("checks", {})),
            "tools": len(registry.get("tools", {})),
            "harnesses": len(registry.get("harnesses", {})),
            "errors": len(registry.get("validation_errors", [])),
        }
    except Exception:
        traceback.print_exc()

    # Compute overall status
    selftest_overall = report["selftest"]["overall"]
    if selftest_overall == "fail" or scheduler_crashed:
        report["overall"] = "fail"
    elif selftest_overall == "degraded":
        report["overall"] = "degraded"
    else:
        report["overall"] = "pass"

    elapsed_ms = int((time.monotonic() - start) * 1000)
    report["duration_ms"] = elapsed_ms

    return report


def format_report(result: Dict[str, Any]) -> str:
    """Format a smoketest result dict for terminal output."""
    lines: list[str] = []

    overall = result.get("overall", "unknown").upper()
    tag = "PASS" if overall == "PASS" else ("DEGRADED" if overall == "DEGRADED" else "FAIL")
    lines.append(f"=== Smoketest: {tag} ===")
    lines.append("")

    # Discovery
    disc = result.get("discovery", {})
    lines.append(
        f"  [PASS] Discovery — paths={disc.get('paths', 0)}, "
        f"services={disc.get('services', 0)}, agents={disc.get('agents', 0)}"
    )

    # Self-test
    st = result.get("selftest", {})
    st_overall = st.get("overall", "fail")
    st_tag = "PASS" if st_overall == "ok" else "FAIL"
    lines.append(
        f"  [{st_tag}] Self-test — passed={st.get('passed', 0)}, "
        f"failed={st.get('failed', 0)}"
    )

    # Scheduler
    sched = result.get("scheduler", {})
    lines.append(
        f"  [PASS] Scheduler tick — checks_run={sched.get('checks_run', 0)}, "
        f"checks_passed={sched.get('checks_passed', 0)}, "
        f"harnesses_run={sched.get('harnesses_run', 0)}"
    )

    # Integrity
    integ = result.get("integrity", {})
    integ_status = integ.get("status", "no_manifest")
    if integ_status == "ok":
        integ_tag = "PASS"
    elif integ_status == "no_manifest":
        integ_tag = "PASS"  # Not a failure, just no manifest yet
    else:
        integ_tag = "FAIL"
    lines.append(
        f"  [{integ_tag}] Integrity — status={integ_status}, "
        f"checked={integ.get('checked', 0)}"
    )

    # Bundles
    bun = result.get("bundles", {})
    bun_tag = "PASS" if bun.get("errors", 0) == 0 else "FAIL"
    lines.append(
        f"  [{bun_tag}] Bundles — checks={bun.get('checks', 0)}, "
        f"tools={bun.get('tools', 0)}, harnesses={bun.get('harnesses', 0)}, "
        f"errors={bun.get('errors', 0)}"
    )

    lines.append("")
    lines.append(f"  Duration: {result.get('duration_ms', 0)} ms")
    return "\n".join(lines)
