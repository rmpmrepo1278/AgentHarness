#!/usr/bin/env python3
"""graphify MCP server with gateway registration."""
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

# Ensure graphify is in path
os.environ.setdefault("PATH", "/home/rohit/.local/bin:" + os.environ.get("PATH", ""))

# Import mcp_base
sys.path.insert(0, "/mcp-base")
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("graphify-mcp")

# Get port from env
MCP_PORT = int(os.environ.get("MCP_PORT", "8110"))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8090")
GRAPH_PATH = os.environ.get("GRAPHIFY_GRAPH_PATH", "/data/graphify-out/graph.json")

# Define tools that graphify-mcp exposes
TOOLS = [
    {
        "name": "graphify_query",
        "description": "Query the knowledge graph for code relationships, dependencies, and patterns",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query about the codebase"},
                "graph_path": {"type": "string", "default": GRAPH_PATH}
            },
            "required": ["query"]
        }
    },
    {
        "name": "graphify_path",
        "description": "Find shortest path between two nodes in the knowledge graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source node ID"},
                "target": {"type": "string", "description": "Target node ID"},
                "graph_path": {"type": "string", "default": GRAPH_PATH}
            },
            "required": ["source", "target"]
        }
    },
    {
        "name": "graphify_explain",
        "description": "Get plain-language explanation of a node and its neighbors",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node ID to explain"},
                "graph_path": {"type": "string", "default": GRAPH_PATH}
            },
            "required": ["node"]
        }
    },
    {
        "name": "graphify_build",
        "description": "Build or update the knowledge graph from a local codebase",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to codebase to index"},
                "graph_path": {"type": "string", "default": GRAPH_PATH}
            },
            "required": ["path"]
        }
    },
    {
        "name": "graphify_add_url",
        "description": "Fetch a URL and add to the knowledge graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch and add"},
                "graph_path": {"type": "string", "default": GRAPH_PATH}
            },
            "required": ["url"]
        }
    }
]

class GraphifyMCP(MCPServer):
    def __init__(self):
        super().__init__("graphify-mcp", MCP_PORT, TOOLS)
        self._graphify_process = None

    def _handle_jsonrpc(self, body: dict):
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id", 1)

        if method == "tools/list":
            return {"jsonrpc": "2.0", "result": {"tools": self.tools}, "id": req_id}

        if method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            return self._call_tool(tool_name, args, req_id)

        if method == "initialize":
            return {"jsonrpc": "2.0", "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "graphify-mcp", "version": "0.9.32"}
            }, "id": req_id}

        return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": req_id}

    def _call_tool(self, tool_name: str, args: dict, req_id: int):
        try:
            if tool_name == "graphify_query":
                result = self._run_graphify(["query", args["query"], "--graph", args.get("graph_path", GRAPH_PATH)])
            elif tool_name == "graphify_path":
                result = self._run_graphify(["path", args["source"], args["target"], "--graph", args.get("graph_path", GRAPH_PATH)])
            elif tool_name == "graphify_explain":
                result = self._run_graphify(["explain", args["node"], "--graph", args.get("graph_path", GRAPH_PATH)])
            elif tool_name == "graphify_build":
                result = self._run_graphify(["update", args["path"], "--graph", args.get("graph_path", GRAPH_PATH)])
            elif tool_name == "graphify_add_url":
                result = self._run_graphify(["add", args["url"], "--dir", str(Path(args.get("graph_path", GRAPH_PATH)).parent / "raw")])
            else:
                return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}, "id": req_id}

            return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": result}]}, "id": req_id}

        except Exception as e:
            log.error("Tool %s failed: %s", tool_name, e)
            return {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": req_id}

    def _run_graphify(self, cmd_args: list) -> str:
        """Run graphify CLI and return output."""
        try:
            # Ensure graph directory exists
            Path(GRAPH_PATH).parent.mkdir(parents=True, exist_ok=True)

            cmd = ["graphify"] + cmd_args

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd="/data")
            if proc.returncode != 0:
                return f"Error (exit {proc.returncode}): {proc.stderr}"
            return proc.stdout or "Done"
        except subprocess.TimeoutExpired:
            return "Error: Timeout"
        except Exception as e:
            return f"Error: {e}"


def main():
    # Ensure graph directory exists
    Path(GRAPH_PATH).parent.mkdir(parents=True, exist_ok=True)

    server = GraphifyMCP()
    # Start registration thread
    reg_thread = threading.Thread(target=server._register_with_gateway, daemon=True)
    reg_thread.start()

    # Start HTTP server
    server.start()

if __name__ == "__main__":
    main()
