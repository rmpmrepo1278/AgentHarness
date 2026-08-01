"""Codebase Memory MCP wrapper. Exposes codebase-memory-mcp as an HTTP MCP server."""
from __future__ import annotations
import json
import logging
import os
import subprocess

from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("codebase-memory-mcp")

MCP_BINARY = os.environ.get("MCP_BINARY", "/home/rohit/.local/bin/codebase-memory-mcp")


def _run_tool(tool_name: str, tool_args: dict) -> dict:
    input_data = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args,
        },
    })
    try:
        result = subprocess.run(
            [MCP_BINARY],
            input=input_data,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HOME": "/home/rohit"},
        )
        response = json.loads(result.stdout)
        if "result" in response:
            content = response["result"].get("content", [])
            if content:
                text = content[0].get("text", "")
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"output": text}
            return response["result"]
        elif "error" in response:
            return {"error": response["error"].get("message", str(response["error"]))}
        return {"error": f"stderr: {result.stderr[:500]}" if result.stderr else "Unknown error"}
    except subprocess.TimeoutExpired:
        return {"error": "Tool execution timed out after 120s"}
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse response: {e}", "stdout": result.stdout[:2000], "stderr": result.stderr[:1000]}
    except Exception as e:
        return {"error": str(e)}


def index_repository(args: dict) -> dict:
    repo_path = args.get("repo_path", "")
    if not repo_path:
        return {"error": "repo_path is required"}
    return _run_tool("index_repository", {"repo_path": repo_path})


def search_graph(args: dict) -> dict:
    return _run_tool("search_graph", args)


def query_graph(args: dict) -> dict:
    return _run_tool("query_graph", args)


def trace_path(args: dict) -> dict:
    return _run_tool("trace_path", args)


def get_code_snippet(args: dict) -> dict:
    return _run_tool("get_code_snippet", args)


def get_graph_schema(args: dict) -> dict:
    return _run_tool("get_graph_schema", args)


def get_architecture(args: dict) -> dict:
    return _run_tool("get_architecture", args)


def search_code(args: dict) -> dict:
    return _run_tool("search_code", args)


def list_projects(args: dict) -> dict:
    return _run_tool("list_projects", args)


def delete_project(args: dict) -> dict:
    return _run_tool("delete_project", args)


def index_status(args: dict) -> dict:
    return _run_tool("index_status", args)


def detect_changes(args: dict) -> dict:
    return _run_tool("detect_changes", args)


TOOL_SCHEMAS = [
    {
        "name": "index_repository",
        "description": "Index a repository into the codebase memory graph. Creates AST nodes, dependencies, and knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Absolute path to the repository directory"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_graph",
        "description": "Search the codebase graph for nodes matching a query. Returns matching code entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_graph",
        "description": "Query the codebase graph with a natural language question.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "trace_path",
        "description": "Trace a path through the codebase graph from one node to another.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "Starting node"},
                "to": {"type": "string", "description": "Target node"},
                "max_depth": {"type": "integer", "description": "Max traversal depth (default: 10)"},
            },
            "required": ["from", "to"],
        },
    },
    {
        "name": "get_code_snippet",
        "description": "Retrieve a specific code snippet from the graph by node ID or file path with line numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID in the graph"},
                "path": {"type": "string", "description": "File path (alternative to node_id)"},
                "start_line": {"type": "integer", "description": "Start line number"},
                "end_line": {"type": "integer", "description": "End line number"},
            },
        },
    },
    {
        "name": "get_graph_schema",
        "description": "Get the schema/structure of the codebase graph.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_architecture",
        "description": "Get the high-level architecture of the codebase.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name (optional)"},
            },
        },
    },
    {
        "name": "search_code",
        "description": "Full-text search across all indexed code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_projects",
        "description": "List all indexed projects in the codebase memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_project",
        "description": "Delete an indexed project from the codebase memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name to delete"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "index_status",
        "description": "Get the indexing status of all projects or a specific project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name (optional)"},
            },
        },
    },
    {
        "name": "detect_changes",
        "description": "Detect changes in indexed repositories since last index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name (optional)"},
            },
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8127"))
    server = MCPServer(name="codebase-memory", port=port, tools=TOOL_SCHEMAS)
    server.register_handler("index_repository", index_repository)
    server.register_handler("search_graph", search_graph)
    server.register_handler("query_graph", query_graph)
    server.register_handler("trace_path", trace_path)
    server.register_handler("get_code_snippet", get_code_snippet)
    server.register_handler("get_graph_schema", get_graph_schema)
    server.register_handler("get_architecture", get_architecture)
    server.register_handler("search_code", search_code)
    server.register_handler("list_projects", list_projects)
    server.register_handler("delete_project", delete_project)
    server.register_handler("index_status", index_status)
    server.register_handler("detect_changes", detect_changes)
    log.info("Codebase Memory MCP starting on :%d with %d tools", port, len(TOOL_SCHEMAS))
    server.start()


if __name__ == "__main__":
    main()
