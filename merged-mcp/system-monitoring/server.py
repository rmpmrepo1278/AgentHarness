#!/usr/bin/env python3
"""System Monitoring Combined Server.

Combines: docker-ops, homelab-ops, network-mcp
Ports:
- 8103: Docker ops
- 8106: Homelab ops
- 8104: Network MCP
"""
from __future__ import annotations
import os
import sys
import signal
import multiprocessing

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

# Import all handlers and their TOOL_SCHEMAS
from system_monitoring.docker_ops import (
    docker_status, docker_container_logs, docker_container_restart, docker_container_stop,
    docker_list_images, docker_prune, TOOL_SCHEMAS as DOCKER_TOOL_SCHEMAS
)
from system_monitoring.homelab_ops import (
    docker_status as homelab_docker_status,
    docker_container_logs, docker_container_restart, docker_container_stop,
    disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health,
    TOOL_SCHEMAS as HOMELAB_TOOL_SCHEMAS
)
from system_monitoring.network_mcp import (
    port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip,
    TOOL_SCHEMAS as NETWORK_TOOL_SCHEMAS
)

from mcp_base import MCPServer


def create_docker_server():
    """Create Docker MCP server on port 8103."""
    s = MCPServer(name="system-docker", port=8103, tools=DOCKER_TOOL_SCHEMAS)
    s.register_handler("docker_status", docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("docker_images", docker_list_images)
    s.register_handler("docker_prune", docker_prune)
    return s


def create_network_server():
    """Create Network MCP server on port 8105."""
    s = MCPServer(name="system-network", port=8107, tools=NETWORK_TOOL_SCHEMAS)
    s.register_handler("port_scan", port_scan)
    s.register_handler("dns_lookup", dns_lookup)
    s.register_handler("check_internet", check_internet)
    s.register_handler("ping_host", ping_host)
    s.register_handler("list_network_services", list_network_services)
    s.register_handler("external_ip", external_ip)
    return s


def create_homelab_ops_server():
    """Create Homelab Ops MCP server on port 8106."""
    s = MCPServer(name="system-homelab-ops", port=8106, tools=HOMELAB_TOOL_SCHEMAS)
    s.register_handler("docker_status", homelab_docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("disk_usage", disk_usage)
    s.register_handler("system_load", system_load)
    s.register_handler("check_docker_daemon", check_docker_daemon)
    s.register_handler("check_systemd_services", check_systemd_services)
    s.register_handler("container_health", container_health)
    return s


def run_server(server_factory, name):
    """Run a server in a subprocess."""
    server = server_factory()
    print(f"[{name}] Starting on port {server.port}")
    server.start()


def main():
    """Run all servers in separate processes."""
    multiprocessing.set_start_method("spawn")

    # Create server factories
    servers = [
        (create_docker_server, "docker-mcp"),
        (create_network_server, "network-mcp"),
        (create_homelab_ops_server, "homelab-ops-mcp"),
    ]

    # Start each in a separate process
    processes = []
    for factory, name in servers:
        p = multiprocessing.Process(target=run_server, args=(factory, name), name=name)
        p.start()
        processes.append(p)

    def shutdown(signum, frame):
        print("\nShutting down system monitoring services...")
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    p.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Wait for all processes
    for p in processes:
        p.join()


if __name__ == "__main__":
    main()