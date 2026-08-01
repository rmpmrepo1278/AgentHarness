#!/usr/bin/env python3
"""System Monitoring MCP Server - Docker, systemd, disk, health checks."""
from __future__ import annotations
import os
import sys
import subprocess
import logging
from pathlib import Path

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("system-monitoring")

HOST_IP = os.environ.get("HOST_IP", "192.168.29.10")
MCP_BASE_DIR = os.environ.get("MCP_BASE_DIR", "/mcp-base")

# Import the modules
from system_monitoring.docker_ops import docker_status, docker_container_logs, docker_container_restart, docker_container_stop, docker_list_images, docker_prune
from system_monitoring.homelab_ops import docker_status as hl_docker_status, docker_container_logs as hl_docker_container_logs, docker_container_restart as hl_docker_container_restart, docker_container_stop as hl_docker_container_stop, disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health
from system_monitoring.network_mcp import port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip

TOOL_SCHEMAS = [
    {"name": "docker_status", "description": "Get Docker status - running/unhealthy counts, disk usage.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "docker_container_logs", "description": "Get container logs.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "tail": {"type": "string", "default": "50"}}, "required": ["container"]}},
    {"name": "docker_container_restart", "description": "Restart a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "docker_container_stop", "description": "Stop a container.", "inputSchema": {"type": "object", "properties": {"container": {"type": "string"}, "timeout": {"type": "string", "default": "30"}}, "required": ["container"]}},
    {"name": "docker_images", "description": "List Docker images.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "docker_prune", "description": "Prune unused Docker images.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "disk_usage", "description": "Get disk usage for key mount points.",        "inputSchema": {"type": "object", "properties": {"mounts": {"type": "string", "default": "/,/home,/mnt/usb,/var/lib/docker"}}}},
    {"name": "system_load", "description": "Get system load, memory, uptime.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_docker_daemon", "description": "Check if Docker daemon is responsive.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_systemd_services", "description": "Check status of systemd services.", "inputSchema": {"type": "object", "properties": {"services": {"type": "string", "default": "hermes-gateway,hermes-scheduler,hermes-mind-loop,bmoe-server,health-dashboard,tdai-gateway,opencode-web,loopany"}}}},
    {"name": "container_health", "description": "Check health of all containers.",        "inputSchema": {"type": "object", "properties": {}}},
    {"name": "port_scan", "description": "Scan for open ports on a host.", "inputSchema": {"type": "object", "properties": {"host": {"type": "string", "description": "Host to scan (default: homelab IP)"}, "ports": {"type": "string", "description": "Comma-separated ports to check"}}}},
    {"name": "dns_lookup", "description": "Perform DNS lookup for a domain name.",
        "inputSchema": {"type": "object", "properties": {"domain": {"type": "string", "description": "Domain to look up"}}, "required": ["domain"]}},
    {"name": "check_internet", "description": "Check if the homelab has internet connectivity.",
        "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ping_host", "description": "Ping a host and check latency.",
        "inputSchema": {"type": "object", "properties": {"host": {"type": "string", "description": "Host or IP to ping"}}, "required": ["host"]}},
    {"name": "list_network_services", "description": "List all services listening on the homelab.",
        "inputSchema": {"type": "object", "properties": {}}},
    {"name": "external_ip", "description": "Get the homelab's public/external IP address.",
        "inputSchema": {"type": "object", "properties": {}}},
]


def main():
    from mcp_base import MCPServer
    import os
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("system-monitoring")

    port = int(os.environ.get("MCP_PORT", "8103"))
    s = __import__('mcp_base', fromlist=['MCPServer']).MCPServer(name="system-monitoring", port=port, tools=TOOL_SCHEMAS)

    # Register handlers
    from system_monitoring.docker_ops import docker_status, docker_container_logs, docker_container_restart, docker_container_stop, docker_list_images, docker_prune
    from system_monitoring.homelab_ops import disk_usage, system_load, check_docker_daemon, check_systemd_services, container_health
    from system_monitoring.network_mcp import port_scan, dns_lookup, check_internet, ping_host, list_network_services, external_ip

    s.register_handler("docker_status", docker_status)
    s.register_handler("docker_container_logs", docker_container_logs)
    s.register_handler("docker_container_restart", docker_container_restart)
    s.register_handler("docker_container_stop", docker_container_stop)
    s.register_handler("docker_images", lambda a: {"status": "ok", "images": []})
    s.register_handler("docker_prune", lambda a: {"status": "ok"})
    s.register_handler("disk_usage", disk_usage)
    s.register_handler("system_load", system_load)
    s.register_handler("check_docker_daemon", check_docker_daemon)
    s.register_handler("check_systemd_services", check_systemd_services)
    s.register_handler("container_health", container_health)
    s.register_handler("port_scan", port_scan)
    s.register_handler("dns_lookup", dns_lookup)
    s.register_handler("check_internet", check_internet)
    s.register_handler("ping_host", ping_host)
    s.register_handler("list_network_services", list_network_services)
    s.register_handler("external_ip", external_ip)

    log.info(f"System Monitoring MCP starting on :{port}")
    s.start()


if __name__ == "__main__":
    main()