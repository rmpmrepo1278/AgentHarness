#!/usr/bin/env python3
"""AgentHarness CLI — manage your infrastructure agent framework."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _data_dir() -> str:
    """Resolve the data directory from env or default."""
    import os
    return os.environ.get("AH_DATA_DIR", str(Path.home() / ".agentharness"))


def _install_dir() -> str:
    """Resolve the install directory (project root)."""
    return str(Path(__file__).resolve().parent)


# ------------------------------------------------------------------
# Command handlers — lazy imports to avoid import-time failures
# ------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> None:
    """Show current agent harness status."""
    from core.discovery.state import StateManager

    data_dir = _data_dir()
    sm = StateManager(data_dir=data_dir)
    state = sm.read()

    paths = state.get("paths", {})
    hardware = state.get("hardware", {})
    services = state.get("services", {})
    agents = state.get("agents", {})
    stale = sm.ensure_fresh()

    print("=== AgentHarness Status ===")
    print(f"  Install dir : {paths.get('install_dir', _install_dir())}")
    print(f"  Data dir    : {data_dir}")
    print(f"  Last updated: {state.get('last_updated', 'never')}")
    print()

    # Hardware
    ram_mb = hardware.get("ram_total_mb", "?")
    cpu_count = hardware.get("cpu_count", "?")
    print(f"  RAM         : {ram_mb} MB")
    print(f"  CPU cores   : {cpu_count}")
    print()

    # Docker containers
    containers = services.get("docker_containers", [])
    print(f"  Docker containers: {len(containers)}")
    for c in containers:
        if isinstance(c, dict):
            print(f"    - {c.get('name', c.get('id', '?'))}")
        else:
            print(f"    - {c}")
    print()

    # LLM servers
    llm_servers = services.get("llm_servers", [])
    print(f"  LLM servers : {len(llm_servers)}")
    for s in llm_servers:
        if isinstance(s, dict):
            print(f"    - {s.get('name', s.get('url', '?'))}")
        else:
            print(f"    - {s}")
    print()

    # Agents
    agent_list = agents.get("agents", []) if isinstance(agents, dict) else []
    print(f"  Agents      : {len(agent_list)}")
    for a in agent_list:
        if isinstance(a, dict):
            print(f"    - {a.get('name', '?')}")
        else:
            print(f"    - {a}")
    print()

    # Stale paths
    if stale:
        print(f"  Stale paths ({len(stale)}):")
        for key in stale:
            print(f"    - {key}")
    else:
        print("  Stale paths : none")


def cmd_discover(args: argparse.Namespace) -> None:
    """Run full discovery and print summary."""
    from core.discovery.engine import run_discovery

    print("Running discovery...")
    state = run_discovery()

    paths = state.get("paths", {})
    hardware = state.get("hardware", {})
    services = state.get("services", {})
    agents = state.get("agents", {})

    print("Discovery complete.")
    print(f"  Paths found    : {len(paths)}")
    print(f"  Hardware keys  : {len(hardware)}")
    print(f"  Service keys   : {len(services)}")
    print(f"  Agent keys     : {len(agents)}")
    print(f"  Last updated   : {state.get('last_updated', '?')}")


def cmd_health(args: argparse.Namespace) -> None:
    """Placeholder for health checks."""
    print("Health checks not yet implemented (Phase B adds scheduler)")


def cmd_bundle_list(args: argparse.Namespace) -> None:
    """Load registry and show bundle dirs, totals, and validation errors."""
    from core.registry.loader import load_registry

    install_dir = _install_dir()
    bundles_dir = Path(install_dir) / "bundles"

    registry = load_registry(bundles_dir)

    checks = registry.get("checks", {})
    tools = registry.get("tools", {})
    harnesses = registry.get("harnesses", {})
    errors = registry.get("validation_errors", [])
    warnings = registry.get("warnings", [])

    print("=== Bundle Registry ===")
    print(f"  Bundles dir       : {bundles_dir}")
    print(f"  Checks registered : {len(checks)}")
    print(f"  Tools registered  : {len(tools)}")
    print(f"  Harnesses         : {len(harnesses)}")
    print()

    if checks:
        print("  Checks:")
        for name in sorted(checks):
            print(f"    - {name}")
    if tools:
        print("  Tools:")
        for name in sorted(tools):
            print(f"    - {name}")
    if harnesses:
        print("  Harnesses:")
        for name in sorted(harnesses):
            print(f"    - {name}")

    if warnings:
        print()
        print(f"  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    ! {w}")

    if errors:
        print()
        print(f"  Validation errors ({len(errors)}):")
        for e in errors:
            print(f"    X {e}")


def cmd_selftest(args: argparse.Namespace) -> None:
    """Run self-test and show PASS/FAIL per check."""
    from core.resilience.selftest import run_selftest

    data_dir = _data_dir()
    result = run_selftest(data_dir)

    overall = result["overall"]
    checks = result["checks"]

    print(f"=== Self-Test: {overall.upper()} ===")
    for c in checks:
        status = "PASS" if c["status"] == "ok" else "FAIL"
        req = " (required)" if c.get("required") else ""
        line = f"  [{status}] {c['name']}{req}"
        if c.get("error"):
            line += f" — {c['error']}"
        print(line)


def cmd_circuits(args: argparse.Namespace) -> None:
    """Show open circuit breakers."""
    from core.resilience.circuit_breaker import CircuitBreaker

    data_dir = _data_dir()
    cb = CircuitBreaker(data_dir=data_dir)
    open_circuits = cb.get_open_circuits()

    if open_circuits:
        print(f"Open circuit breakers ({len(open_circuits)}):")
        for name in open_circuits:
            print(f"  - {name}")
    else:
        print("No open circuit breakers.")


def cmd_audit(args: argparse.Namespace) -> None:
    """Show last 20 entries from exec_audit.jsonl."""
    import collections

    data_dir = _data_dir()
    audit_file = Path(data_dir) / "exec_audit.jsonl"

    if not audit_file.exists():
        print("No audit log found.")
        return

    # Read last 20 lines
    lines: list[str] = []
    try:
        with open(audit_file, "r") as f:
            lines = collections.deque(f, maxlen=20)  # type: ignore[assignment]
    except OSError as exc:
        print(f"Error reading audit log: {exc}")
        return

    if not lines:
        print("Audit log is empty.")
        return

    print(f"=== Last {len(lines)} Audit Entries ===")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", "?")
            tool = entry.get("tool", "?")
            exit_code = entry.get("exit_code", "?")
            trigger = entry.get("trigger", "?")
            print(f"  [{ts}] {tool} (trigger={trigger}, exit={exit_code})")
        except json.JSONDecodeError:
            print(f"  [malformed] {line[:80]}")


def cmd_integrity(args: argparse.Namespace) -> None:
    """Verify file integrity against manifest."""
    from core.security.integrity import verify_integrity

    install_dir = _install_dir()
    manifest_path = str(Path(install_dir) / "data" / "integrity_manifest.json")

    result = verify_integrity(install_dir, manifest_path)
    status = result["status"]
    checked = result["checked"]
    modified = result.get("modified", [])
    missing = result.get("missing", [])

    print(f"=== Integrity Check: {status.upper()} ===")
    print(f"  Files checked: {checked}")

    if status == "no_manifest":
        print("  No manifest found — run 'scripts/generate_manifest.py' first.")
        return

    if modified:
        print(f"  Modified ({len(modified)}):")
        for p in modified:
            print(f"    ~ {p}")

    if missing:
        print(f"  Missing ({len(missing)}):")
        for p in missing:
            print(f"    ! {p}")

    if not modified and not missing:
        print("  All files match manifest.")


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentharness",
        description="AgentHarness — manage your infrastructure agent framework.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show current status")
    sub.add_parser("discover", help="Run full discovery")
    sub.add_parser("health", help="Run health checks (placeholder)")

    bundle_parser = sub.add_parser("bundle", help="Bundle management")
    bundle_sub = bundle_parser.add_subparsers(dest="bundle_command")
    bundle_sub.add_parser("list", help="List registered bundles")

    sub.add_parser("selftest", help="Run startup self-test")
    sub.add_parser("circuits", help="Show open circuit breakers")
    sub.add_parser("audit", help="Show recent audit log entries")
    sub.add_parser("integrity", help="Verify file integrity")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        None: cmd_status,
        "status": cmd_status,
        "discover": cmd_discover,
        "health": cmd_health,
        "selftest": cmd_selftest,
        "circuits": cmd_circuits,
        "audit": cmd_audit,
        "integrity": cmd_integrity,
    }

    if args.command == "bundle":
        if args.bundle_command == "list":
            cmd_bundle_list(args)
        else:
            parser.parse_args(["bundle", "--help"])
    elif args.command in dispatch:
        dispatch[args.command](args)
    else:
        dispatch[None](args)


if __name__ == "__main__":
    main()
