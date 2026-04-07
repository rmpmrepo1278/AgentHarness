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


def cmd_budget(args: argparse.Namespace) -> int:
    """Show LLM budget status."""
    from core.discovery.state import StateManager
    from core.providers.budget import BudgetTracker

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir", ".")

    bt = BudgetTracker(data_dir=data_dir)
    print(bt.daily_report())
    return 0


def cmd_migrate_scheduler(args: argparse.Namespace) -> int:
    """Migrate from bash scheduler (cron) to Python scheduler (systemd)."""
    from core.discovery.state import StateManager
    import subprocess

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir")

    if not data_dir:
        print("Error: Run 'agentharness discover' first.")
        return 1

    if args.rollback:
        print("Rolling back to bash scheduler...")
        # Re-enable cron
        subprocess.run("(crontab -l 2>/dev/null; echo '*/15 * * * * ...') | crontab -", shell=True)
        # Disable systemd
        subprocess.run(["systemctl", "--user", "stop", "agentharness-scheduler"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", "agentharness-scheduler"], capture_output=True)
        print("Rolled back to bash scheduler (cron).")
        return 0

    print("Migrating to Python scheduler...")
    print("Step 1: Removing cron entry for scheduler.sh...")
    subprocess.run("crontab -l 2>/dev/null | grep -v 'scheduler.sh' | crontab -", shell=True)

    print("Step 2: Testing one scheduler tick...")
    from core.scheduler.scheduler import Scheduler
    try:
        sched = Scheduler(data_dir=data_dir)
        result = sched.tick()
        print(f"  Tick OK: {result['checks_run']} checks, {result['harnesses_run']} harnesses, window={result['window']}")
    except Exception as e:
        print(f"  Tick FAILED: {e}")
        print("  Aborting migration. Cron entry was removed — re-add manually or run --rollback.")
        return 1

    print("Step 3: Enable systemd service...")
    subprocess.run(["systemctl", "--user", "enable", "agentharness-scheduler"], capture_output=True)
    subprocess.run(["systemctl", "--user", "start", "agentharness-scheduler"], capture_output=True)

    print("Migration complete. Python scheduler is now active.")
    print("  Check status: systemctl --user status agentharness-scheduler")
    print("  Rollback:     agentharness migrate-scheduler --rollback")
    return 0


def cmd_proposals(args: argparse.Namespace) -> int:
    """List pending proposals."""
    from core.approval.gateway import ApprovalGateway
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")

    gw = ApprovalGateway(proposals_dir=proposals_dir)
    pending = gw.list_pending()

    if not pending:
        print("No pending proposals.")
        return 0

    print(f"{'ID':<8} {'Tool':<25} {'Type':<20} {'Reason'}")
    print("-" * 80)
    for p in pending:
        print(f"{p.proposal_id:<8} {p.tool_name:<25} {p.proposal_type:<20} {p.reason}")

    print(f"\n{len(pending)} pending proposal(s)")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Approve a pending proposal."""
    from core.approval.gateway import ApprovalGateway
    from core.approval.auth import validate_and_approve, ApprovalValidationError
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")
    gw = ApprovalGateway(proposals_dir=proposals_dir)

    try:
        proposal = validate_and_approve(gw, args.proposal_id, source="cli")
        print(f"Approved proposal {proposal.proposal_id} ({proposal.tool_name})")
        print(f"Will execute in next scheduler tick.")
        return 0
    except ApprovalValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_reject(args: argparse.Namespace) -> int:
    """Reject a pending proposal."""
    from core.approval.gateway import ApprovalGateway
    from core.approval.auth import validate_and_reject, ApprovalValidationError
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")
    gw = ApprovalGateway(proposals_dir=proposals_dir)

    reason = args.reason or ""
    try:
        proposal = validate_and_reject(
            gw, args.proposal_id, reason=reason, source="cli",
        )
        print(f"Rejected proposal {proposal.proposal_id} ({proposal.tool_name})")
        if reason:
            print(f"Reason: {reason}")
        return 0
    except ApprovalValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_briefing(args: argparse.Namespace) -> int:
    """Show the latest infrastructure briefing."""
    from core.discovery.state import StateManager
    import json as _json

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir", ".")
    briefings_dir = Path(data_dir) / "briefings"

    if not briefings_dir.is_dir():
        print("No briefings yet. Run the scheduler to generate one.")
        return 0

    # Find latest briefing
    files = sorted(briefings_dir.glob("*.json"), reverse=True)
    if not files:
        print("No briefings found.")
        return 0

    briefing = _json.loads(files[0].read_text())
    # Format for terminal
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=data_dir)
    print(d.format_telegram(briefing))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run diagnostic collector or auto-fix diagnosis."""
    from core.discovery.state import StateManager

    sm = StateManager()
    state = sm.read()
    data_dir = state.get("paths", {}).get("data_dir", ".")

    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=data_dir)
    context = dc.collect()

    if getattr(args, "auto_fix", False):
        from core.doctor.autofix import AutoFixer
        af = AutoFixer(data_dir=data_dir)
        result = af.diagnose_and_propose()
        if result["success"]:
            print("Diagnosis:", result.get("diagnosis", "No issues"))
        else:
            print("Error:", result.get("error", "Unknown"))
    else:
        print(dc.format_prompt(context))
    return 0


def cmd_smoketest(args: argparse.Namespace) -> int:
    """Run full post-deploy smoketest."""
    from core.doctor.smoketest import run_smoketest, format_report

    data_dir = _data_dir()
    result = run_smoketest(data_dir=data_dir)
    print(format_report(result))
    return 0 if result["overall"] != "fail" else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Run pre-deploy validation and print report."""
    from core.doctor.validate_remote import validate_local, format_report

    results = validate_local()
    print(format_report(results))
    return 0


def cmd_setup_coding_tool(args: argparse.Namespace) -> int:
    """Generate and write Aider config, print setup script."""
    from core.tools.setup_aider import (
        generate_aider_config,
        write_aider_config,
        generate_setup_script,
    )

    provider = getattr(args, "provider", "groq")
    config = generate_aider_config(provider=provider)
    config_path = write_aider_config(config)
    print("Aider config written to:", config_path)
    print()
    print(generate_setup_script(provider=provider))
    return 0


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
    sub.add_parser("briefing", help="Show latest infrastructure briefing")
    sub.add_parser("integrity", help="Verify file integrity")
    sub.add_parser("budget", help="Show LLM budget status")
    migrate_parser = sub.add_parser("migrate-scheduler", help="Migrate to Python scheduler")
    migrate_parser.add_argument("--rollback", action="store_true")

    sub.add_parser("proposals", help="List pending approval proposals")

    approve_parser = sub.add_parser("approve", help="Approve a proposal")
    approve_parser.add_argument("proposal_id", help="Proposal ID to approve")

    reject_parser = sub.add_parser("reject", help="Reject a proposal")
    reject_parser.add_argument("proposal_id", help="Proposal ID to reject")
    reject_parser.add_argument("--reason", "-r", default="", help="Rejection reason")

    doctor_parser = sub.add_parser("doctor", help="Run diagnostics or auto-fix")
    doctor_parser.add_argument("--auto-fix", action="store_true", dest="auto_fix",
                               help="Run LLM-based diagnosis and propose fixes")

    sub.add_parser("smoketest", help="Run full post-deploy smoketest")
    sub.add_parser("validate", help="Run pre-deploy validation checks")

    setup_ct_parser = sub.add_parser("setup-coding-tool",
                                     help="Generate Aider coding tool config")
    setup_ct_parser.add_argument("--provider", default="groq",
                                 help="LLM provider (default: groq)")

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
        "briefing": cmd_briefing,
        "integrity": cmd_integrity,
        "budget": cmd_budget,
        "migrate-scheduler": cmd_migrate_scheduler,
        "proposals": cmd_proposals,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "doctor": cmd_doctor,
        "smoketest": cmd_smoketest,
        "validate": cmd_validate,
        "setup-coding-tool": cmd_setup_coding_tool,
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
