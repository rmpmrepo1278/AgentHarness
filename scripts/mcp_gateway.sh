#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# mcp_gateway.sh — Discover, catalog, and bridge MCP servers for Chaguli
#
# Since OpenClaw doesn't natively support MCP yet, this script:
#   1. Discovers all MCP servers on the homelab (Docker, standalone)
#   2. Catalogs their tools/capabilities
#   3. Generates OpenClaw SKILL.md files that wrap MCP tools as exec commands
#   4. Optionally runs a lightweight HTTP bridge that Chaguli can curl
#
# When OpenClaw adds native MCP, this becomes a simple config migration.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f "${AH_DATA_DIR}/chaguli_paths.env" ] && source "${AH_DATA_DIR}/chaguli_paths.env"

MCP_CATALOG="${AH_DATA_DIR}/mcp_catalog.json"
MCP_SKILLS_PREFIX="mcp"

# =============================================================================
# Discover MCP servers
# =============================================================================
discover_mcp_servers() {
    log_info "Scanning for MCP servers..."

    local servers=()

    # Method 1: Check Docker containers for MCP-related labels and env vars
    docker ps --format '{{.Names}}' 2>/dev/null | while read -r container; do
        [ -z "${container}" ] && continue

        # Check env vars for MCP indicators
        local envs
        envs=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" 2>/dev/null || true)

        local is_mcp=false
        local mcp_port=""

        # Look for MCP-related env vars
        if echo "${envs}" | grep -qi "MCP\|MODEL_CONTEXT_PROTOCOL\|mcp_server\|MCP_PORT"; then
            is_mcp=true
        fi

        # Check labels
        local labels
        labels=$(docker inspect --format '{{json .Config.Labels}}' "${container}" 2>/dev/null || echo "{}")
        if echo "${labels}" | grep -qi "mcp"; then
            is_mcp=true
        fi

        # Check if container image name suggests MCP
        local image
        image=$(docker inspect --format '{{.Config.Image}}' "${container}" 2>/dev/null || echo "")
        if echo "${image}" | grep -qi "mcp"; then
            is_mcp=true
        fi

        if [ "${is_mcp}" = true ]; then
            # Get the exposed port
            mcp_port=$(docker port "${container}" 2>/dev/null | grep -oP '\d+$' | head -1 || echo "")
            log_ok "MCP container found: ${container} (port: ${mcp_port:-unknown})"
            echo "${container}|docker|${mcp_port}|${image}"
        fi
    done > /tmp/mcp_discovered.txt

    # Method 2: Check common MCP ports with JSON-RPC probe
    for port in 3000 3001 3333 4000 4001 5000 5001 5555 6000 8000 8001 9000 9001; do
        # Skip ports we know are other services
        local known_service
        known_service=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP '(?<=users:\(\().*?(?=,)' || echo "")

        # Try MCP initialize handshake
        local response
        response=$(curl -sf --max-time 3 -X POST "http://localhost:${port}" \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{"roots":{"listChanged":true}},"clientInfo":{"name":"agentharness","version":"1.0"}},"id":1}' \
            2>/dev/null || echo "")

        if echo "${response}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'result' in d:
        print('MCP_SERVER')
except: pass
" 2>/dev/null | grep -q "MCP_SERVER"; then
            log_ok "MCP server on port ${port} (${known_service:-standalone})"
            echo "localhost:${port}|standalone|${port}|${known_service}" >> /tmp/mcp_discovered.txt
        fi
    done

    # Method 3: Check for MCP server configs in known locations
    for config_dir in /opt/*/mcp* /opt/mcp* /home/*/mcp* ~/.config/mcp*; do
        if [ -d "${config_dir}" ] && [ -f "${config_dir}/package.json" ]; then
            log_info "MCP server project found: ${config_dir}"
            echo "${config_dir}|project|0|$(basename ${config_dir})" >> /tmp/mcp_discovered.txt
        fi
    done

    # Method 4: Check for homelab-mcp-bundle
    if [ -d /opt/homelab-mcp-bundle ] || [ -d /opt/mcp-bundle ]; then
        local bundle_dir
        bundle_dir=$(ls -d /opt/homelab-mcp-bundle /opt/mcp-bundle 2>/dev/null | head -1)
        log_ok "Homelab MCP bundle found: ${bundle_dir}"
        echo "${bundle_dir}|bundle|0|homelab-mcp-bundle" >> /tmp/mcp_discovered.txt
    fi
}

# =============================================================================
# Probe MCP server capabilities
# =============================================================================
probe_mcp_server() {
    local host="$1"
    local port="$2"

    if [ -z "${port}" ] || [ "${port}" = "0" ]; then
        return
    fi

    local url="http://${host}:${port}"

    # Initialize
    local init_response
    init_response=$(curl -sf --max-time 5 -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{"roots":{"listChanged":true}},"clientInfo":{"name":"agentharness","version":"1.0"}},"id":1}' \
        2>/dev/null || echo "{}")

    # List tools
    local tools_response
    tools_response=$(curl -sf --max-time 5 -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}' \
        2>/dev/null || echo "{}")

    # List resources
    local resources_response
    resources_response=$(curl -sf --max-time 5 -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"resources/list","params":{},"id":3}' \
        2>/dev/null || echo "{}")

    # List prompts
    local prompts_response
    prompts_response=$(curl -sf --max-time 5 -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"prompts/list","params":{},"id":4}' \
        2>/dev/null || echo "{}")

    python3 -c "
import json

init = json.loads('''${init_response}''') if '''${init_response}''' else {}
tools = json.loads('''${tools_response}''') if '''${tools_response}''' else {}
resources = json.loads('''${resources_response}''') if '''${resources_response}''' else {}
prompts = json.loads('''${prompts_response}''') if '''${prompts_response}''' else {}

server_info = init.get('result', {}).get('serverInfo', {})
capabilities = init.get('result', {}).get('capabilities', {})
tool_list = tools.get('result', {}).get('tools', [])
resource_list = resources.get('result', {}).get('resources', [])
prompt_list = prompts.get('result', {}).get('prompts', [])

print(json.dumps({
    'url': '${url}',
    'server_name': server_info.get('name', 'unknown'),
    'server_version': server_info.get('version', ''),
    'capabilities': list(capabilities.keys()),
    'tools': [{'name': t.get('name',''), 'description': t.get('description',''), 'parameters': t.get('inputSchema',{})} for t in tool_list],
    'resources': [{'uri': r.get('uri',''), 'name': r.get('name',''), 'description': r.get('description','')} for r in resource_list],
    'prompts': [{'name': p.get('name',''), 'description': p.get('description','')} for p in prompt_list]
}, indent=2))
" 2>/dev/null
}

# =============================================================================
# Generate OpenClaw skill for an MCP server
# =============================================================================
generate_mcp_skill() {
    local server_json="$1"

    python3 << PYEOF
import json, os

server = json.loads('''${server_json}''')
name = server.get('server_name', 'unknown').replace(' ', '-').lower()
url = server.get('url', '')
tools = server.get('tools', [])
resources = server.get('resources', [])

if not tools and not resources:
    print(f"Skipping {name}: no tools or resources")
    exit(0)

skill_dir = "${OPENCLAW_SKILLS_DIR:-/tmp}/${MCP_SKILLS_PREFIX}-" + name
os.makedirs(skill_dir, exist_ok=True)

# Check for existing user skill
skill_path = os.path.join(skill_dir, "SKILL.md")
if os.path.exists(skill_path):
    existing = open(skill_path).read()
    if "Auto-generated by AgentHarness" not in existing:
        print(f"{name}: User-modified skill exists — skipping")
        exit(0)

skill = f"""---
name: mcp-{name}
description: MCP bridge for {server.get('server_name', name)} — {len(tools)} tools, {len(resources)} resources. Auto-generated by AgentHarness.
requires:
  binaries: ["curl", "python3"]
---

# {server.get('server_name', name)} (MCP Bridge)

This service exposes an MCP server at `{url}`. Since OpenClaw doesn't natively support MCP yet, use these curl commands to call MCP tools.

"""

    # Generate tool sections
    if tools:
        skill += "## Available Tools\n\n"
        for tool in tools:
            tool_name = tool.get('name', '?')
            desc = tool.get('description', '')
            params = tool.get('parameters', {})
            properties = params.get('properties', {})
            required = params.get('required', [])

            skill += f"### {tool_name}\n"
            skill += f"{desc}\n\n"

            if properties:
                skill += "Parameters:\n"
                for pname, pinfo in properties.items():
                    req = " (required)" if pname in required else ""
                    ptype = pinfo.get('type', '?')
                    pdesc = pinfo.get('description', '')
                    skill += f"- `{pname}` ({ptype}{req}): {pdesc}\n"
                skill += "\n"

            # Generate the curl command
            param_json = json.dumps({p: f"VALUE" for p in properties.keys()})
            skill += f\"""```bash
curl -sf -X POST {url} \\
  -H "Content-Type: application/json" \\
  -d '{{"jsonrpc":"2.0","method":"tools/call","params":{{"name":"{tool_name}","arguments":{param_json}}},"id":1}}'
```

\"\"\"

    # Generate resource sections
    if resources:
        skill += "## Available Resources\\n\\n"
        for res in resources:
            uri = res.get('uri', '?')
            rname = res.get('name', uri)
            rdesc = res.get('description', '')
            skill += f"### {rname}\\n"
            skill += f"{rdesc}\\n\\n"
            skill += f\"""```bash
curl -sf -X POST {url} \\
  -H "Content-Type: application/json" \\
  -d '{{"jsonrpc":"2.0","method":"resources/read","params":{{"uri":"{uri}"}},"id":1}}'
```

\"\"\"

    with open(skill_path, 'w') as f:
        f.write(skill)

    print(f"{name}: {len(tools)} tools, {len(resources)} resources → {skill_path}")
PYEOF
}

# =============================================================================
# Build catalog and generate skills
# =============================================================================
build_catalog() {
    log_info "Building MCP catalog..."

    local catalog='{"servers": [], "discovered_at": "'$(date -Iseconds)'"}'
    local servers_json="["
    local first=true

    if [ -f /tmp/mcp_discovered.txt ]; then
        while IFS='|' read -r source type port image; do
            [ -z "${source}" ] && continue

            local host="localhost"
            [[ "${source}" == *":"* ]] && host=$(echo "${source}" | cut -d: -f1)
            [ "${port}" = "0" ] && continue

            log_info "Probing MCP server: ${source} (port ${port})..."
            local probe_result
            probe_result=$(probe_mcp_server "${host}" "${port}" 2>/dev/null || echo "{}")

            if [ -n "${probe_result}" ] && [ "${probe_result}" != "{}" ]; then
                [ "${first}" = true ] && first=false || servers_json+=","
                servers_json+="${probe_result}"

                # Generate skill
                generate_mcp_skill "${probe_result}"
            fi
        done < /tmp/mcp_discovered.txt
    fi

    servers_json+="]"

    echo "{\"discovered_at\": \"$(date -Iseconds)\", \"servers\": ${servers_json}}" | \
        python3 -c "import sys,json; json.dump(json.load(sys.stdin), open('${MCP_CATALOG}', 'w'), indent=2)" 2>/dev/null

    rm -f /tmp/mcp_discovered.txt
}

# =============================================================================
# Print summary
# =============================================================================
print_summary() {
    if [ ! -f "${MCP_CATALOG}" ]; then
        log_info "No MCP catalog generated"
        return
    fi

    python3 -c "
import json
catalog = json.load(open('${MCP_CATALOG}'))
servers = catalog.get('servers', [])
print(f'\nMCP Servers: {len(servers)}')
for s in servers:
    name = s.get('server_name', 'unknown')
    tools = len(s.get('tools', []))
    resources = len(s.get('resources', []))
    url = s.get('url', '?')
    print(f'  {name} ({url}): {tools} tools, {resources} resources')
    for t in s.get('tools', [])[:5]:
        print(f'    - {t[\"name\"]}: {t.get(\"description\", \"\")[:60]}')
    if tools > 5:
        print(f'    ... and {tools - 5} more')
" 2>/dev/null
}

# =============================================================================
main() {
    log_header "MCP Gateway"
    ensure_dir "${AH_DATA_DIR}"

    discover_mcp_servers
    build_catalog
    print_summary

    local server_count
    server_count=$(python3 -c "import json; print(len(json.load(open('${MCP_CATALOG}')).get('servers',[])))" 2>/dev/null || echo "0")

    if [ "${server_count}" -gt 0 ]; then
        log_ok "Found ${server_count} MCP server(s). Skills generated in OpenClaw."
        log_info "Chaguli can now call MCP tools via curl commands."
    else
        log_info "No MCP servers found. They'll be discovered automatically when deployed."
    fi
}

main "$@"
