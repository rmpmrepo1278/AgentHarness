"""Network MCP server. Port scanning, DNS lookups, service discovery, connectivity checks."""
from __future__ import annotations
import os, sys, socket, subprocess, logging, json
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("network-mcp")

HOST_IP = os.environ.get("HOST_IP", "192.168.29.10")

def port_scan(args):
    """Scan open ports on a host."""
    host = args.get("host", HOST_IP)
    ports = args.get("ports", "80,443,8080,8443,3000,5000,8000,8081,8085,8093,8096,9000")
    port_list = [int(p.strip()) for p in str(ports).split(",") if p.strip().isdigit()]
    open_ports = []
    for port in port_list:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex((host, port)) == 0:
                open_ports.append(port)
            s.close()
        except Exception: pass
    return {"host": host, "open_ports": open_ports, "scanned": len(port_list)}

def dns_lookup(args):
    """Perform DNS lookup for a domain."""
    domain = args.get("domain", "")
    if not domain: return {"error": "domain required"}
    try:
        ips = socket.getaddrinfo(domain, None)
        unique_ips = list(set(addr[4][0] for addr in ips))
        return {"domain": domain, "ips": unique_ips}
    except Exception as e:
        return {"error": str(e)}

def check_internet(args):
    """Check if the homelab has internet connectivity."""
    targets = [("8.8.8.8", 53), ("1.1.1.1", 53), ("google.com", 443)]
    results = []
    for host, port in targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.close()
            results.append({"host": host, "port": port, "status": "reachable"})
        except Exception:
            results.append({"host": host, "port": port, "status": "unreachable"})
    online = sum(1 for r in results if r["status"] == "reachable")
    return {"online": online > 0, "checks": results}

def ping_host(args):
    """Ping a host and return latency."""
    host = args.get("host", "")
    if not host: return {"error": "host required"}
    try:
        result = subprocess.run(["ping", "-c", "3", "-W", "2", host], capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            stats = lines[-1] if lines else ""
            return {"host": host, "status": "reachable", "stats": stats}
        return {"host": host, "status": "unreachable"}
    except Exception as e:
        return {"error": str(e)}

def list_network_services(args):
    """List all services listening on the homelab."""
    try:
        result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10)
        services = []
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 4:
                addr = parts[3]
                services.append({"listen": addr})
        return {"services": services, "count": len(services)}
    except Exception as e:
        return {"error": str(e)}

def external_ip(args):
    """Get the homelab's external/public IP address."""
    import requests as _r
    try:
        resp = _r.get("https://api.ipify.org?format=json", timeout=5)
        return {"external_ip": resp.json().get("ip", "unknown")}
    except Exception as e:
        return {"error": str(e)}

TOOL_SCHEMAS = [
    {"name": "port_scan", "description": "Scan for open ports on a host.", "inputSchema": {"type": "object", "properties": {"host": {"type": "string", "description": "Host to scan (default: homelab IP)"}, "ports": {"type": "string", "description": "Comma-separated ports to check"}}}},
    {"name": "dns_lookup", "description": "Perform DNS lookup for a domain name.", "inputSchema": {"type": "object", "properties": {"domain": {"type": "string", "description": "Domain to look up"}}, "required": ["domain"]}},
    {"name": "check_internet", "description": "Check if the homelab has internet connectivity.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ping_host", "description": "Ping a host and check latency.", "inputSchema": {"type": "object", "properties": {"host": {"type": "string", "description": "Host or IP to ping"}}, "required": ["host"]}},
    {"name": "list_network_services", "description": "List all network services listening on the homelab.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "external_ip", "description": "Get the homelab's public/external IP address.", "inputSchema": {"type": "object", "properties": {}}},
]

def main():
    port = int(os.environ.get("MCP_PORT", "8103"))
    s = MCPServer(name="network", port=port, tools=TOOL_SCHEMAS)
    for n, fn in [("port_scan", port_scan), ("dns_lookup", dns_lookup), ("check_internet", check_internet), ("ping_host", ping_host), ("list_network_services", list_network_services), ("external_ip", external_ip)]:
        s.register_handler(n, fn)
    log.info(f"Network MCP starting on :{port}")
    s.start()

if __name__ == "__main__": main()
