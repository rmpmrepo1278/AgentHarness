"""Bridge: JSON-RPC HTTP <-> code-review-graph CLI."""
import json
import logging
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("code-review-bridge")

BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8096"))

class BridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json_error(400, "Invalid JSON")
            return

        method = req.get("method", "")
        params = req.get("params", {})
        req_id = req.get("id", 1)

        try:
            if method == "tools/list":
                result = self._list_tools()
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = self._call_tool(tool_name, arguments)
            elif method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "code-review-graph", "version": "0.9.32"}
                }
            else:
                result = {"error": {"code": -32601, "message": f"Method not found: {method}"}}

            response = {"jsonrpc": "2.0", "result": result, "id": req_id}
            self._json_response(200, response)
        except Exception as e:
            log.error("Bridge error: %s", e)
            self._json_error(500, str(e))

    def _list_tools(self):
        return [
            {"name": "query", "description": "Query the knowledge graph for code relationships", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
            {"name": "impact", "description": "Analyze change impact on the codebase", "inputSchema": {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}, "base": {"type": "string"}}, "required": ["files"]}},
            {"name": "search", "description": "Search the knowledge graph", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
            {"name": "dead_code", "description": "Find functions/classes with no callers or test references", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "flows", "description": "Show call flows for a function", "inputSchema": {"type": "object", "properties": {"function": {"type": "string"}}, "required": ["function"]}},
            {"name": "large_functions", "description": "Find large functions exceeding a line threshold", "inputSchema": {"type": "object", "properties": {"min_lines": {"type": "number", "default": 50}}}},
            {"name": "communities", "description": "List code communities/clusters", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "architecture", "description": "Architecture overview", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "detect_changes", "description": "Detect change impact against the graph", "inputSchema": {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}, "base": {"type": "string"}}}},
            {"name": "enrich", "description": "Enrich text with graph context", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
            {"name": "repos", "description": "List registered repositories", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "register", "description": "Register a repository", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"}}}},
        ]

    def _call_tool(self, tool_name, arguments):
        """Call code-review-graph CLI using container Python."""
        tool_name = tool_name.replace("_", "-")
        cmd = ["/usr/local/bin/code-review-graph", tool_name]

        if tool_name == "query":
            subcmd = arguments.get("subcommand", "callers_of")
            target = arguments.get("target", arguments.get("query", ""))
            cmd.extend([subcmd, target])
        elif tool_name == "impact":
            for f in arguments.get("files", []):
                cmd.extend(["--files", f])
            if "base" in arguments:
                cmd.extend(["--base", arguments["base"]])
        elif tool_name == "search":
            subcmd = arguments.get("subcommand", "callers_of")
            target = arguments.get("target", arguments.get("query", ""))
            cmd.extend([subcmd, target])
        elif tool_name == "flows":
            cmd.append(arguments.get("function", ""))
        elif tool_name == "large-functions":
            cmd.extend(["--min-lines", str(arguments.get("min_lines", 50))])
        elif tool_name == "detect-changes":
            for f in arguments.get("files", []):
                cmd.extend(["--files", f])
            if "base" in arguments:
                cmd.extend(["--base", arguments["base"]])
        elif tool_name == "enrich":
            cmd.extend(["--text", arguments.get("text", "")])
        elif tool_name in ("repos", "register"):
            if tool_name == "register":
                cmd.extend(["--repo", arguments.get("path", "/home/rohit")])
                if "name" in arguments:
                    cmd.extend(["--name", arguments["name"]])

        env = os.environ.copy()

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd="/home/rohit", env=env)
            if proc.returncode != 0:
                return {"error": proc.stderr[:500] or f"Exit code {proc.returncode}"}
            return proc.stdout.strip() or "OK"
        except subprocess.TimeoutExpired:
            return {"error": "Timeout after 120s"}
        except Exception as e:
            return {"error": str(e)}

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _json_error(self, code, message):
        self._json_response(code, {"error": message})

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_error(404, "Not found")

    def log_message(self, format, *args):
        log.info("%s - %s", self.address_string(), format % args)

def main():
    server = HTTPServer(("127.0.0.1", BRIDGE_PORT), BridgeHandler)
    log.info("code-review-graph bridge listening on :%d", BRIDGE_PORT)
    server.serve_forever()

if __name__ == "__main__":
    main()
