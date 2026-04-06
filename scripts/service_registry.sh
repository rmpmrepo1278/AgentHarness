#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# service_registry.sh — Discover and catalog all service APIs/endpoints
#
# Scans every running Docker container and known service for:
#   - REST API endpoints (health, status, management)
#   - MCP server endpoints
#   - WebSocket endpoints
#   - Admin/management UIs
#
# Produces: /opt/agentharness/service_registry.json
# This is the single source of truth for "what can Chaguli talk to"
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REGISTRY="/opt/agentharness/service_registry.json"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

# =============================================================================
# Probe a URL and return status + response info
# =============================================================================
probe_endpoint() {
    local url="$1"
    local timeout="${2:-5}"

    local result
    result=$(curl -sf --max-time "${timeout}" -o /dev/null -w '%{http_code}|%{content_type}|%{size_download}' "${url}" 2>/dev/null || echo "000||0")

    local code content_type size
    IFS='|' read -r code content_type size <<< "${result}"
    echo "${code}|${content_type}|${size}"
}

# =============================================================================
# Discover API endpoints for a container
# =============================================================================
discover_container_apis() {
    local container="$1"
    local container_lower
    container_lower=$(echo "${container}" | tr '[:upper:]' '[:lower:]')

    # Get all exposed ports
    local ports
    ports=$(docker port "${container}" 2>/dev/null | while read -r line; do
        echo "${line}" | grep -oP '\d+$'
    done | sort -u)

    [ -z "${ports}" ] && return

    local apis="[]"

    for port in ${ports}; do
        # Common API paths to probe
        local paths=(
            "/"
            "/api"
            "/api/v1"
            "/api/health"
            "/health"
            "/healthz"
            "/status"
            "/v1"
            "/v1/models"
            "/metrics"
            "/swagger"
            "/docs"
            "/openapi.json"
            "/api/swagger.json"
        )

        # Service-specific paths based on container name
        case "${container_lower}" in
            *portainer*)
                paths+=("/api/status" "/api/endpoints" "/api/stacks")
                ;;
            *pihole*)
                paths+=("/admin/api.php" "/admin/api.php?summary")
                ;;
            *jellyfin*)
                paths+=("/System/Info/Public" "/health")
                ;;
            *immich*)
                paths+=("/api/server-info/ping" "/api/server-info/version")
                ;;
            *nextcloud*)
                paths+=("/ocs/v2.php/cloud/capabilities?format=json" "/status.php")
                ;;
            *grafana*)
                paths+=("/api/health" "/api/org" "/api/dashboards/home")
                ;;
            *n8n*)
                paths+=("/api/v1/workflows" "/healthz")
                ;;
            *sonarr*|*radarr*|*prowlarr*|*lidarr*|*readarr*)
                paths+=("/api/v3/system/status" "/api/v1/system/status" "/ping")
                ;;
            *npm*|*nginx*proxy*)
                paths+=("/api" "/api/nginx/proxy-hosts")
                ;;
            *homarr*)
                paths+=("/api/health")
                ;;
            *stump*)
                paths+=("/api/v1/libraries")
                ;;
            *openclaw*)
                paths+=("/api/health")
                ;;
            *searxng*|*searx*)
                paths+=("/search?q=test&format=json" "/config")
                ;;
        esac

        for path in "${paths[@]}"; do
            local url="http://localhost:${port}${path}"
            local result
            result=$(probe_endpoint "${url}" 3)

            local code content_type size
            IFS='|' read -r code content_type size <<< "${result}"

            # Record successful endpoints
            if [ "${code}" != "000" ] && [ "${code}" != "404" ] && [ "${code}" != "405" ]; then
                local is_api=false
                local is_json=false
                local is_health=false
                local is_docs=false

                [[ "${content_type}" == *json* ]] && is_json=true && is_api=true
                [[ "${path}" == */health* ]] && is_health=true
                [[ "${path}" == */swagger* ]] || [[ "${path}" == */docs* ]] || [[ "${path}" == */openapi* ]] && is_docs=true
                [[ "${path}" == */api/* ]] && is_api=true
                [[ "${path}" == */v1/* ]] || [[ "${path}" == */v2/* ]] || [[ "${path}" == */v3/* ]] && is_api=true

                # Get a sample of the response for JSON APIs
                local sample=""
                if [ "${is_json}" = true ] && [ "${size}" -gt 0 ] && [ "${size}" -lt 10000 ]; then
                    sample=$(curl -sf --max-time 3 "${url}" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    # Extract just the keys/structure, not values (for schema detection)
    if isinstance(d, dict):
        print(json.dumps({k: type(v).__name__ for k, v in list(d.items())[:15]}))
    elif isinstance(d, list) and d:
        print(json.dumps({'_array_of': type(d[0]).__name__, '_count': len(d)}))
    else:
        print(json.dumps({'_type': type(d).__name__}))
except:
    print('{}')
" 2>/dev/null || echo "{}")
                fi

                python3 -c "
import json
print(json.dumps({
    'url': '${url}',
    'port': ${port},
    'path': '${path}',
    'http_code': ${code},
    'is_json': ${is_json},
    'is_api': ${is_api},
    'is_health': ${is_health},
    'is_docs': ${is_docs},
    'response_schema': ${sample:-'{}'}
}))
" 2>/dev/null
            fi
        done
    done
}

# =============================================================================
# Scan for MCP server endpoints
# =============================================================================
discover_mcp_endpoints() {
    log_info "Scanning for MCP server endpoints..."

    # Check common MCP ports and paths
    local mcp_ports=(3000 3001 3333 4000 5000 5001 8000 8001 9000)

    for port in "${mcp_ports[@]}"; do
        # MCP servers respond to POST with JSON-RPC
        local result
        result=$(curl -sf --max-time 3 -X POST "http://localhost:${port}" \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"initialize","params":{"capabilities":{}},"id":1}' \
            2>/dev/null || echo "")

        if echo "${result}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'result' in d and 'capabilities' in d.get('result', {}):
        print('MCP_SERVER')
except:
    pass
" 2>/dev/null | grep -q "MCP_SERVER"; then
            log_ok "Found MCP server on port ${port}"

            # Get server info
            local info
            info=$(curl -sf --max-time 3 -X POST "http://localhost:${port}" \
                -H "Content-Type: application/json" \
                -d '{"jsonrpc":"2.0","method":"initialize","params":{"capabilities":{}},"id":1}' \
                2>/dev/null || echo "{}")

            # List available tools
            local tools
            tools=$(curl -sf --max-time 3 -X POST "http://localhost:${port}" \
                -H "Content-Type: application/json" \
                -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}' \
                2>/dev/null || echo "{}")

            python3 -c "
import json
try:
    info = json.loads('''${info}''')
    tools = json.loads('''${tools}''')
    print(json.dumps({
        'type': 'mcp_server',
        'port': ${port},
        'url': 'http://localhost:${port}',
        'server_info': info.get('result', {}).get('serverInfo', {}),
        'capabilities': info.get('result', {}).get('capabilities', {}),
        'tools': tools.get('result', {}).get('tools', [])
    }))
except:
    pass
" 2>/dev/null
        fi
    done

    # Also scan Docker containers for MCP-related env vars or labels
    docker ps --format '{{.Names}}' 2>/dev/null | while read -r container; do
        local envs
        envs=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" 2>/dev/null || true)
        if echo "${envs}" | grep -qi "MCP\|MODEL_CONTEXT_PROTOCOL"; then
            log_info "Container ${container} has MCP-related env vars"
            local mcp_port
            mcp_port=$(docker port "${container}" 2>/dev/null | grep -oP '\d+$' | head -1 || echo "")
            [ -n "${mcp_port}" ] && echo "MCP_CONTAINER|${container}|${mcp_port}"
        fi
    done
}

# =============================================================================
# Build the service registry
# =============================================================================
build_registry() {
    log_header "Building Service Registry"

    ensure_dir /opt/agentharness

    local all_services="[]"

    # Scan all running containers
    local containers
    containers=$(docker ps --format '{{.Names}}' 2>/dev/null || true)

    local registry_items=()

    while IFS= read -r container; do
        [ -z "${container}" ] && continue
        log_info "Scanning: ${container}..."

        local container_lower
        container_lower=$(echo "${container}" | tr '[:upper:]' '[:lower:]')

        # Get container metadata
        local image
        image=$(docker inspect --format='{{.Config.Image}}' "${container}" 2>/dev/null || echo "unknown")
        local status
        status=$(docker inspect --format='{{.State.Status}}' "${container}" 2>/dev/null || echo "unknown")
        local ports_raw
        ports_raw=$(docker port "${container}" 2>/dev/null || echo "")

        # Discover APIs
        local endpoints
        endpoints=$(discover_container_apis "${container}" 2>/dev/null || echo "")

        # Categorize service
        local category="other"
        case "${container_lower}" in
            *portainer*)  category="management" ;;
            *pihole*)     category="network" ;;
            *npm*|*nginx*proxy*) category="network" ;;
            *jellyfin*)   category="media" ;;
            *immich*)     category="media" ;;
            *nextcloud*)  category="storage" ;;
            *stump*)      category="media" ;;
            *grafana*)    category="monitoring" ;;
            *n8n*)        category="automation" ;;
            *sonarr*|*radarr*|*prowlarr*|*lidarr*|*readarr*) category="media" ;;
            *homarr*)     category="management" ;;
            *openclaw*) category="ai" ;;
            *searxng*)    category="search" ;;
            *llama*|*ik-llama*) category="ai" ;;
        esac

        # Determine what Chaguli can do with this service
        local chaguli_capabilities=""
        if echo "${endpoints}" | grep -q '"is_api": true\|"is_api": True'; then
            chaguli_capabilities+="query,"
        fi
        if echo "${endpoints}" | grep -q '"is_health": true\|"is_health": True'; then
            chaguli_capabilities+="health_check,"
        fi
        case "${container_lower}" in
            *portainer*)  chaguli_capabilities+="manage_containers,view_stacks," ;;
            *pihole*)     chaguli_capabilities+="dns_stats,block_domains," ;;
            *jellyfin*)   chaguli_capabilities+="media_info," ;;
            *grafana*)    chaguli_capabilities+="view_dashboards,query_metrics," ;;
            *n8n*)        chaguli_capabilities+="trigger_workflows,list_workflows," ;;
            *sonarr*)     chaguli_capabilities+="search_shows,view_calendar," ;;
            *radarr*)     chaguli_capabilities+="search_movies," ;;
            *prowlarr*)   chaguli_capabilities+="search_indexers," ;;
            *npm*)        chaguli_capabilities+="manage_proxies,view_routes," ;;
            *nextcloud*)  chaguli_capabilities+="file_operations," ;;
            *immich*)     chaguli_capabilities+="photo_search," ;;
            *searxng*)    chaguli_capabilities+="web_search," ;;
        esac
        chaguli_capabilities=$(echo "${chaguli_capabilities}" | sed 's/,$//')

        # Collect endpoint list
        local endpoint_array="[]"
        if [ -n "${endpoints}" ]; then
            endpoint_array=$(echo "${endpoints}" | python3 -c "
import sys, json
items = []
for line in sys.stdin:
    line = line.strip()
    if line:
        try:
            items.append(json.loads(line))
        except:
            pass
print(json.dumps(items))
" 2>/dev/null || echo "[]")
        fi

        # Add to registry
        python3 -c "
import json
print(json.dumps({
    'container': '${container}',
    'image': '${image}',
    'status': '${status}',
    'category': '${category}',
    'chaguli_capabilities': '${chaguli_capabilities}'.split(',') if '${chaguli_capabilities}' else [],
    'endpoints': ${endpoint_array},
    'ports': '''${ports_raw}'''.strip().split('\n') if '''${ports_raw}'''.strip() else []
}))
" 2>/dev/null

    done <<< "${containers}"
}

# =============================================================================
# Assemble final registry
# =============================================================================
assemble_registry() {
    log_info "Assembling service registry..."

    {
        build_registry
        discover_mcp_endpoints
    } | python3 -c "
import sys, json
from datetime import datetime

services = []
mcp_servers = []

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        item = json.loads(line)
        if item.get('type') == 'mcp_server':
            mcp_servers.append(item)
        elif 'container' in item:
            services.append(item)
    except:
        # Handle MCP_CONTAINER lines
        if line.startswith('MCP_CONTAINER|'):
            parts = line.split('|')
            mcp_servers.append({
                'type': 'mcp_container',
                'container': parts[1] if len(parts) > 1 else '',
                'port': int(parts[2]) if len(parts) > 2 else 0
            })

# Count capabilities
all_capabilities = set()
for svc in services:
    all_capabilities.update(svc.get('chaguli_capabilities', []))

registry = {
    'updated_at': datetime.now().isoformat(),
    'total_services': len(services),
    'total_mcp_servers': len(mcp_servers),
    'total_api_endpoints': sum(len(s.get('endpoints', [])) for s in services),
    'all_capabilities': sorted(list(all_capabilities)),
    'services': services,
    'mcp_servers': mcp_servers
}

json.dump(registry, open('${REGISTRY}', 'w'), indent=2)
print(f'Registry: {len(services)} services, {len(mcp_servers)} MCP servers, {sum(len(s.get(\"endpoints\", [])) for s in services)} API endpoints')
print(f'Chaguli capabilities: {\", \".join(sorted(all_capabilities))}')
" 2>/dev/null
}

# =============================================================================
# Print summary
# =============================================================================
print_summary() {
    python3 << 'PYEOF'
import json

reg = json.load(open("/opt/agentharness/service_registry.json"))

print(f"\n  Services: {reg['total_services']}")
print(f"  MCP Servers: {reg['total_mcp_servers']}")
print(f"  API Endpoints: {reg['total_api_endpoints']}")
print(f"\n  Chaguli can: {', '.join(reg['all_capabilities'])}")

print("\n  By category:")
cats = {}
for svc in reg['services']:
    cat = svc.get('category', 'other')
    cats.setdefault(cat, []).append(svc['container'])
for cat in sorted(cats.keys()):
    print(f"    {cat}: {', '.join(cats[cat])}")

if reg['mcp_servers']:
    print(f"\n  MCP Servers:")
    for mcp in reg['mcp_servers']:
        tools = mcp.get('tools', [])
        name = mcp.get('server_info', {}).get('name', mcp.get('container', f"port {mcp.get('port')}"))
        print(f"    {name}: {len(tools)} tool(s)")
        for t in tools[:5]:
            print(f"      - {t.get('name', '?')}: {t.get('description', '')[:60]}")
PYEOF
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "Service Registry"

    ensure_dir /opt/agentharness

    assemble_registry
    print_summary

    log_ok "Registry: ${REGISTRY}"
}

main "$@"
