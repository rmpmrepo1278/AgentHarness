"""Base class for MCP servers.
Handles gateway registration with retry-backoff, health endpoint, and JSON-RPC dispatch."""
from __future__ import annotations
import json
import os
import time
import signal
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

log = logging.getLogger("mcp_base")


class MCPServer:
    """Base MCP server with self-registration, health, and JSON-RPC tool dispatch."""

    def __init__(self, name: str, port: int, tools: list[dict]):
        self.name = name
        self.port = port
        self.tools = tools
        self._tool_handlers: dict[str, callable] = {}
        self._start_time = time.time()
        self._gateway_url = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8096")
        self._container_name = os.environ.get("HOSTNAME", name)
        self._stop_event = threading.Event()

    def register_handler(self, tool_name: str, handler: callable):
        """Register a handler function for a tool."""
        self._tool_handlers[tool_name] = handler

    def _register_with_gateway(self):
        """Register with gateway, retrying with exponential backoff."""
        delays = [10, 20, 40, 60]
        attempt = 0
        while not self._stop_event.is_set():
            try:
                # With host networking, use 127.0.0.1 instead of container hostname
                address = os.environ.get("MCP_ADDRESS", f"http://127.0.0.1:{self.port}")
                resp = requests.post(
                    f"{self._gateway_url}/register",
                    json={
                        "name": self.name,
                        "address": address,
                        "container_name": self._container_name,
                        "tools": self.tools,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    log.info(f"Registered with gateway: {resp.json()}")
                    return True
                log.warning(f"Gateway returned {resp.status_code}: {resp.text}")
            except requests.RequestException as e:
                delay = delays[min(attempt, len(delays) - 1)]
                log.warning(f"Gateway unreachable ({e}), retrying in {delay}s...")
                self._stop_event.wait(delay)
                attempt += 1
        return False

    def _deregister(self):
        """Gracefully deregister from gateway."""
        try:
            requests.post(
                f"{self._gateway_url}/deregister",
                json={"name": self.name},
                timeout=5,
            )
            log.info("Deregistered from gateway")
        except requests.RequestException:
            pass

    def _handle_jsonrpc(self, body: dict) -> dict:
        """Handle a JSON-RPC 2.0 request."""
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id", 1)

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": self.name, "version": "1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.tools},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = self._tool_handlers.get(tool_name)
            if not handler:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
            try:
                result = handler(arguments)
                return {"jsonrpc": "2.0", "id": req_id, "result": result}
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(e)},
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def start(self):
        """Start the MCP server: register with gateway, serve JSON-RPC + health."""
        mcp = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    body = json.dumps({
                        "status": "ok",
                        "name": mcp.name,
                        "tools": len(mcp.tools),
                        "uptime": int(time.time() - mcp._start_time),
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                result = mcp._handle_jsonrpc(req)
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        reg_thread = threading.Thread(target=self._register_with_gateway, daemon=True)
        reg_thread.start()

        def shutdown_handler(sig, frame):
            log.info("Shutting down...")
            self._deregister()
            self._stop_event.set()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        server = HTTPServer(("0.0.0.0", self.port), Handler)
        log.info(f"MCP server '{self.name}' listening on :{self.port}")
        server.serve_forever()
