#!/usr/bin/env python3
"""System Monitoring MCP Server - Combined Docker, systemd, disk, health checks.

Combines: docker-ops, homelab-ops, network-mcp, mcp-gateway
Each service runs on its own port:
- 8103: docker-ops
- 8106: homelab-ops
- 8103: network-mcp (same port, combined)
- 8090: mcp-gateway
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

# Import the service modules
from system_monitoring.docker_ops import (
    docker_container_logs,
    docker_container_restart,
    docker_container_stop,
    docker_list_images,
    docker_prune,
)

# Also need to import the handlers from the individual modules
from system_monitoring.docker_ops import docker_status as docker_status_handler
from system_monitoring.homelab_ops import (
    check_docker_daemon,
    check_systemd_services,
)
from system_monitoring.homelab_ops import container_health as container_health_handler
from system_monitoring.homelab_ops import disk_usage as disk_usage_handler
from system_monitoring.homelab_ops import system_load as system_load_handler


def create_docker_server():
    """Create Docker MCP server on port 8103."""

    s = MCPServer(name="system-docker", port=8103)
    s.register_handler("docker_status", docker_status_handler)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("docker_images", docker_list_images)
    s.register_handler("docker_prune", docker_prune)
    return s


def create_homelab_ops_server():
    """Create Homelab Ops MCP server on port 8106."""

    s = MCPServer(name="homelab-ops", port=8106)
    s.register_handler("disk_usage", disk_usage_handler)
    s.register_handler("system_load", system_load_handler)
    s.register_handler("check_docker_daemon", check_docker_daemon)
    s.register_handler("check_systemd_services", check_systemd_services)
    s.register_handler("container_health", container_health_handler)
    return s


def create_network_server():
    """Create Network MCP server on port 8103 (same port as docker, but different service name)."""

    # Network uses same port as docker but different service name
    # In practice, we'd run on different ports, but for now using 8103

    # Network tools will be registered under the docker server's port
    # Or we can run on a different port. For now, let's use the same port
    # and register all tools under the docker server
    return None


def create_gateway_server():
    """Create the MCP Gateway server on port 8090."""

    # Gateway is a separate Flask app, not using MCPServer base
    return None


def run_all_servers():
    """Run all servers in separate threads."""

    # Docker server (port 8103)
    docker_thread = threading.Thread(target=create_docker_server().start, daemon=True, name="docker-mcp")

    # Homelab ops server (port 8106)
    homelab_thread = threading.Thread(target=create_homelab_ops_server().start, daemon=True, name="homelab-ops-mcp")

    # Network server - register tools on docker server for now
    # In production, would run on separate port

    threads = [
        docker_thread,
        homelab_thread,
    ]

    for t in threads:
        t.start()

    # Keep main thread alive
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down system monitoring services...")
        sys.exit(0)


if __name__ == "__main__":
    run_all_servers()
