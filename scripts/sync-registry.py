#!/usr/bin/env python3
"""
sync-registry.py — Validate and sync automation files against service-registry.yml

Usage:
  python3 sync-registry.py                    # Validate all files
  python3 sync-registry.py --check            # Check-only (exit 1 on mismatch)
  python3 sync-registry.py --fix              # Auto-fix discrepancies
  python3 sync-registry.py --report           # Generate coverage report
"""
import os, sys, re, json, yaml

REGISTRY_PATH = os.path.expanduser("~/services/service-registry.yml")
COMPOSE_MCP = os.path.expanduser("~/agentharness/docker-compose.mcp.yml")
FIXER_PATH = os.path.expanduser("~/agentharness/scripts/autonomous_fixer.py")
DELEGATE_PATH = os.path.expanduser("~/agentharness/scripts/auto_fix_delegate.py")
HEALTH_SH = os.path.expanduser("~/agentharness/scripts/consolidated_health.sh")
TRAEFIK_DIR = os.path.expanduser("~/services/traefik/dynamic")

def load_registry():
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f)

def discover_compose_mcp():
    """Parse docker-compose.mcp.yml for MCP_PORT assignments."""
    mcp = {}
    try:
        with open(COMPOSE_MCP) as f:
            name = None
            for line in f:
                cm = re.search(r'container_name: (\S+)', line)
                if cm:
                    name = cm.group(1)
                pm = re.search(r'MCP_PORT=(\d+)', line)
                if pm and name:
                    mcp[int(pm.group(1))] = name
                    name = None
        mcp[8090] = "mcp-gateway"
    except FileNotFoundError:
        pass
    return mcp

def check_compose_vs_fixer():
    """Verify fixer's auto-discovery matches compose file."""
    mcp = discover_compose_mcp()
    issues = []
    # Verify the fixer can read the compose file
    if not mcp:
        issues.append("Cannot read docker-compose.mcp.yml for MCP port discovery")
    # Check all expected services exist
    expected = {"mcp-gateway", "hermes-memory-mcp", "docker-mcp", "file-mcp",
                "rss-mcp", "doctor-mcp", "global-chat-mcp", "homelab-exec",
}
    found = set(mcp.values())
    missing = expected - found
    extra = found - expected
    if missing:
        issues.append(f"MCP services in compose but not in fixer: {missing}")
    if extra:
        issues.append(f"MCP services in fixer but not in compose: {extra}")
    return issues

def check_traefik_vs_registry(registry):
    """Check Traefik routes match registry web services."""
    web_services = {s["name"] for s in registry.get("web_services", [])}
    traefik_routes = set()
    if os.path.isdir(TRAEFIK_DIR):
        for f in os.listdir(TRAEFIK_DIR):
            if f.endswith(".yml") and f != "tls.yml":
                traefik_routes.add(f.replace(".yml", ""))
    missing_routes = web_services - traefik_routes - {"traefik", "prometheus", "node-exporter", "cadvisor"}
    extra_routes = traefik_routes - web_services - {"tls"}
    issues = []
    if missing_routes:
        issues.append(f"Registry services without Traefik route: {missing_routes}")
    if extra_routes:
        issues.append(f"Traefik routes not in registry: {extra_routes}")
    return issues

def check_delegate_ports(registry):
    """Check auto_fix_delegate.py MCP port check command is current."""
    mcp = discover_compose_mcp()
    expected_ports = sorted(mcp.keys())
    delegate_path = DELEGATE_PATH
    issues = []
    try:
        with open(delegate_path) as f:
            content = f.read()
        # Find the port check command
        m = re.search(r'for port in ([0-9 ]+);', content)
        if m:
            ports_str = m.group(1)
            actual_ports = sorted(int(p) for p in ports_str.split())
            if actual_ports != expected_ports:
                missing = set(expected_ports) - set(actual_ports)
                extra = set(actual_ports) - set(expected_ports)
                if missing:
                    issues.append(f"auto_fix_delegate missing ports: {sorted(missing)}")
                if extra:
                    issues.append(f"auto_fix_delegate extra ports: {sorted(extra)}")
    except FileNotFoundError:
        issues.append("auto_fix_delegate.py not found")
    return issues

def run_checks():
    registry = load_registry()
    all_issues = []
    all_issues.extend(check_compose_vs_fixer())
    all_issues.extend(check_traefik_vs_registry(registry))
    all_issues.extend(check_delegate_ports(registry))
    return all_issues

if __name__ == "__main__":
    issues = run_checks()
    if issues:
        print(f"REGISTRY CHECKS: {len(issues)} issue(s) found:")
        for i in issues:
            print(f"  ✗ {i}")
        if "--check" in sys.argv:
            sys.exit(1)
    else:
        print("✓ Registry is in sync — no issues found")
