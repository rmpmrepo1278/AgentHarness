import json
import subprocess
import os
import sys
import threading
import logging
import time

# Add mcp-gateway to path
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger("global-chat-mcp")

class StdioBridge:
    def __init__(self, command):
        log.info(f"Starting stdio process: {' '.join(command)}")
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.id_counter = 0
        self.pending_requests = {}
        self.lock = threading.Lock()
        
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            log.debug(f"STDOUT: {line.strip()}")
            try:
                msg = json.loads(line)
                req_id = msg.get("id")
                if req_id is not None and req_id in self.pending_requests:
                    event, result = self.pending_requests.pop(req_id)
                    result["response"] = msg
                    event.set()
            except json.JSONDecodeError:
                pass

    def _read_stderr(self):
        for line in self.proc.stderr:
            log.error(f"STDERR: {line.strip()}")

    def call(self, method, params, timeout=60):
        with self.lock:
            self.id_counter += 1
            req_id = self.id_counter
            event = threading.Event()
            result = {}
            self.pending_requests[req_id] = (event, result)
            
            req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            log.debug(f"SEND: {json.dumps(req)}")
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            
        if event.wait(timeout=timeout):
            return result["response"]
        return {"error": {"code": -32000, "message": "Timeout"}}

# Retry initialization in case npx is slow
bridge = None
for i in range(3):
    try:
        bridge = StdioBridge(["npx", "-y", "@global-chat/mcp-server"])
        # Wait a bit for npx to install/start
        time.sleep(5)
        
        init_resp = bridge.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bridge", "version": "1.0"}
        })
        if "result" in init_resp:
            log.info(f"Initialized: {init_resp['result'].get('serverInfo', {})}")
            bridge.call("notifications/initialized", {})
            break
        log.warning(f"Initialization attempt {i+1} failed: {init_resp}")
    except Exception as e:
        log.error(f"Initialization attempt {i+1} errored: {e}")
    time.sleep(10)

if not bridge:
    log.critical("Failed to start global-chat-mcp bridge")
    sys.exit(1)

# Get tools
tools_resp = bridge.call("tools/list", {})
tools = tools_resp.get("result", {}).get("tools", [])
log.info(f"Discovered {len(tools)} tools: {[t['name'] for t in tools]}")

def handle_tool(arguments, name):
    log.info(f"Calling tool {name} with args {arguments}")
    resp = bridge.call("tools/call", {"name": name, "arguments": arguments})
    if "error" in resp:
        raise Exception(resp["error"].get("message", "Unknown error"))
    return resp.get("result")

port = int(os.environ.get("MCP_PORT", "8106"))
server = MCPServer(name="global-chat", port=port, tools=tools)

for tool in tools:
    tool_name = tool["name"]
    # Use a factory to avoid closure issues in the loop
    def make_handler(tn):
        return lambda args: handle_tool(args, tn)
    server.register_handler(tool_name, make_handler(tool_name))

log.info(f"Global Chat MCP bridge starting on :{port}")
server.start()
