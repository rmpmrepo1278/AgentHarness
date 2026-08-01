"""Docker MCP server. Provides container management tools via JSON-RPC."""
import os
import sys
import logging

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

from mcp_base import MCPServer
import tools
import templates

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("docker-mcp")

TOOL_SCHEMAS = [
    {
        "name": "list_containers",
        "description": "List all Docker containers (running and stopped). Use to check what's deployed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional name pattern to filter by"},
            },
        },
    },
    {
        "name": "deploy_stack",
        "description": "Deploy a Docker Compose stack. Uses vetted templates when available, requires approval for custom YAML.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Stack name (e.g., 'paperless-ngx')"},
                "template": {"type": "string", "description": "Template name (e.g., 'paperless-ngx')"},
                "compose_yaml": {"type": "string", "description": "Raw docker-compose YAML (when no template exists)"},
                "vars": {"type": "object", "description": "Template variables (e.g., {PORT: '8010'})"},
                "approved": {"type": "boolean", "description": "Set true when user approved LLM-generated YAML"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_container",
        "description": "Stop and remove a Docker container. Cannot remove protected containers (chaguli, mcp-gateway, llama-server).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name to remove"},
                "remove_volumes": {"type": "boolean", "description": "Also remove volumes (default: false)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "container_logs",
        "description": "Get recent logs from a Docker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
                "tail": {"type": "integer", "description": "Number of log lines (default: 50)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "container_status",
        "description": "Get detailed status of a Docker container including health, ports, mounts, and uptime.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "restart_container",
        "description": "Restart a Docker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name to restart"},
            },
            "required": ["name"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8095"))

    templates.start_watcher()
    log.info(f"Templates: {', '.join(templates.list_templates()) or 'none'}")

    server = MCPServer(name="docker", port=port, tools=TOOL_SCHEMAS)

    server.register_handler("list_containers", tools.list_containers)
    server.register_handler("deploy_stack", tools.deploy_stack)
    server.register_handler("remove_container", tools.remove_container)
    server.register_handler("container_logs", tools.container_logs)
    server.register_handler("container_status", tools.container_status)
    server.register_handler("restart_container", tools.restart_container)

    log.info(f"Docker MCP starting on :{port} with {len(TOOL_SCHEMAS)} tools")
    server.start()


if __name__ == "__main__":
    main()
