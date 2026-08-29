"""Base class for MCP servers.
Handles gateway registration with retry-backoff, health endpoint, JSON-RPC dispatch.
Supports both the internal gateway protocol and the MCP Streamable HTTP specification
for direct Claude Code connections."""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

log = logging.getLogger("mcp_base")


class MCPServer:
    """Base MCP server with self-registration, health, and JSON-RPC dispatch.

    Supports two connection modes:
      1. Internal gateway protocol (existing) — registered with gateway on port 8090
      2. MCP Streamable HTTP (new) — direct Claude Code connections via HTTP+SSE
    """

    def __init__(self, name: str, port: int, tools: list[dict]):
        self.name = name
        self.port = port
        self.tools = tools
        self._tool_handlers: dict[str, callable] = {}
        self._start_time = time.time()
        self._gateway_url = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8096")
        self._container_name = os.environ.get("HOSTNAME", name)
        self._stop_event = threading.Event()
        # Simple API key auth (optional — set MCP_API_KEY env var to enable)
        self._api_key = os.environ.get("MCP_API_KEY", "")

    def register_handler(self, tool_name: str, handler: callable):
        """Register a handler function for a tool."""
        self._tool_handlers[tool_name] = handler

    def _check_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        """Check API key authentication. Returns True if allowed."""
        if not self._api_key:
            return True  # No auth configured
        auth = handler.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:] == self._api_key
        # Also check X-API-Key header
        return handler.headers.get("X-API-Key", "") == self._api_key

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
                    log.info("Registered with gateway: %s", resp.json())
                    return True
                log.warning("Gateway returned %s: %s", resp.status_code, resp.text)
            except requests.RequestException as e:
                delay = delays[min(attempt, len(delays) - 1)]
                log.warning("Gateway unreachable (%s), retrying in %ss...", e, delay)
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

    def _handle_jsonrpc(self, body: dict) -> dict | None:
        """Handle a JSON-RPC 2.0 request. None = no response (notification)."""
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

        if method.startswith("notifications/"):
            # Client notification — no response needed
            return None

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
                # MCP expects tool results wrapped in content array
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, default=str)}
                        ],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(e)},
                }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def start(self, register_signals: bool = True):
        """Start the MCP server: register with gateway, serve JSON-RPC + health + SSE.

        Args:
            register_signals: If True, register SIGTERM/SIGINT handlers. Only works in main thread.
        """
        mcp = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                # Auth check
                if not mcp._check_auth(self):
                    self._send_json(401, {"error": "Unauthorized"})
                    return

                if self.path == "/health":
                    self._send_json(200, {
                        "status": "ok",
                        "name": mcp.name,
                        "tools": len(mcp.tools),
                        "uptime": int(time.time() - mcp._start_time),
                    })
                    return

                # MCP Streamable HTTP: GET with Accept: text/event-stream opens SSE
                if self.path in ("/mcp", "/"):
                    accept = self.headers.get("Accept", "")
                    if "text/event-stream" in accept:
                        session_id = self.headers.get("Mcp-Session-Id", str(uuid.uuid4()))
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Mcp-Session-Id", session_id)
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "keep-alive")
                        self.end_headers()
                        # Keep connection alive with periodic pings
                        try:
                            while not mcp._stop_event.is_set():
                                mcp._stop_event.wait(30)
                                self.wfile.write(
                                    f"data: {json.dumps({'type': 'ping'})}\n\n".encode()
                                )
                                self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                # Auth check
                if not mcp._check_auth(self):
                    self._send_json(401, {"error": "Unauthorized"})
                    return

                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                result = mcp._handle_jsonrpc(req)

                # Notifications get 204 No Content
                if result is None:
                    self.send_response(204)
                    self.end_headers()
                    return

                self._send_json(200, result)

            def log_message(self, format, *args):
                pass

        reg_thread = threading.Thread(target=self._register_with_gateway, daemon=True)
        reg_thread.start()

        def shutdown_handler(sig, frame):
            log.info("Shutting down...")
            self._deregister()
            self._stop_event.set()
            raise SystemExit(0)

        if register_signals and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, shutdown_handler)
            signal.signal(signal.SIGINT, shutdown_handler)

        server = HTTPServer(("0.0.0.0", self.port), Handler)
        log.info("MCP server '%s' listening on :%d", self.name, self.port)
        server.serve_forever()
