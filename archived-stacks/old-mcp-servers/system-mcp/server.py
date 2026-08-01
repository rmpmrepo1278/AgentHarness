#!/usr/bin/env python3
"""System Monitoring Combined Server.

Combines: docker, homelab-ops, network, gateway
Ports:
- 8090: MCP Gateway
- 8103: Network
- 8103: Docker (using same port as network, different path)
- 8106: Homelab Ops
"""
from __future__ import annotations
import os
import sys
import threading
import importlib

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

# Import all handlers
from system_monitoring.docker_ops import (
    docker_status, docker_container_logs, docker_container_restart, docker_container_stop,
    docker_list_images, docker_prune
)
from system_monitoring.homelab_ops import (
    docker_status as homelab_docker_status,
    docker_container_logs, docker_container_restart, docker_container_stop,
    disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health
)
from system_monitoring.network_mcp import (
    port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip
)
from system_monitoring.mcp_gateway import app as gateway_app

# Also need to import the handlers from the modules
from system_monitoring.docker_ops import (
    docker_status, docker_container_logs, docker_container_restart, docker_container_stop,
    docker_list_images, docker_prune
)
from system_monitoring.homelab_ops import (
    docker_status as homelab_docker_status,
    docker_container_logs, docker_container_restart, docker_container_stop,
    disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health
)
from system_monitoring.network_mcp import (
    port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip
)


def create_gateway_server():
    """Create Flask gateway server on port 8090."""
    import threading
    from system_monitoring.mcp_gateway import app
    from system_monitoring.mcp_gateway import registry, router, health, gateway_log

    def run_gateway():
        from flask import Flask
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        log = logging.getLogger("gateway")
        log.info("MCP Gateway starting on :8090")
        app.run(host="0.0.0.0", port=8090)

    t = threading.Thread(target=run_gateway, daemon=True, name="mcp-gateway")
    t.start()
    return t


def create_docker_server():
    """Create Docker MCP server on port 8103."""
    from mcp_base import MCPServer
    from system_monitoring.docker_ops import (
        docker_status, docker_container_logs, docker_container_restart, docker_container_stop,
        docker_list_images, docker_prune
    )
    s = MCPServer(name="system-docker", port=8103)
    s.register_handler("docker_status", docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("docker_images", lambda a: {"status": "ok", "images": []})
    s.register_handler("docker_prune", lambda a: {"status": "ok"})
    return s


def create_network_server():
    """Create Network MCP server on port 8103."""
    from mcp_base import MCPServer
    from system_monitoring.network_mcp import (
        port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip
    )
    s = MCPServer(name="system-network", port=8103)
    s.register_handler("port_scan", port_scan)
    s.register_handler("dns_lookup", dns_lookup)
    s.register_handler("check_internet", check_internet)
    s.register_handler("ping_host", ping_host)
    s.register_handler("list_network_services", list_network_services)
    s.register_handler("external_ip", external_ip)
    return s


def create_homelab_ops_server():
    """Create Homelab Ops MCP server on port 8106."""
    from mcp_base import MCPServer
    from system_monitoring.homelab_ops import (
        docker_status as homelab_docker_status,
        docker_container_logs, docker_container_restart, docker_container_stop,
        disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health
    )
    s = MCPServer(name="system-homelab-ops", port=8106)
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


def create_gateway_server():
    """Create Gateway server on port 8090."""
    from system_monitoring.mcp_gateway import app
    import threading
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    def run_gateway():
        app.run(host="0.0.0.0", port=8090)

    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8090), daemon=True, name="mcp-gateway")
    t.start()
    return t


def run_servers():
    """Run all servers in separate threads."""
    import threading

    # Create servers
    gateway_thread = create_gateway_server()
    docker_srv = create_network_server()
    homelab_srv = create_homelab_ops_server()

    threads = [
        gateway_thread,
        threading.Thread(target=docker_srv.start, daemon=True, name="docker-mcp"),
        threading.Thread(target=homelab_srv.start, daemon=True, name="homelab-ops-mcp"),
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
    import sys
    run_servers()