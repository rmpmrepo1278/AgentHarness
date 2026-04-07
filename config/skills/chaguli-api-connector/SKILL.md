---
name: chaguli-api-connector
description: Structured API access to all homelab services — auth-aware, multi-step operations, persistent connections
requires:
  binaries: ["curl", "python3"]
---

# API Connector

You have structured API access to homelab services. Use this skill when you need to do more than basic health checks — actual API operations like creating, modifying, or querying resources.

## Step 1: Load the API Registry

The service registry knows which services have APIs and how to authenticate:

```bash
python3 -c "
import json, os

# Load service registry
reg = json.load(open('/opt/agentharness/service_registry.json'))

# Load API credentials
creds = {}
if os.path.exists('/opt/agentharness/.env'):
    for line in open('/opt/agentharness/.env'):
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            creds[k.strip()] = v.strip()

# Show available APIs with auth status
for svc in reg.get('services', []):
    apis = [e for e in svc.get('endpoints', []) if e.get('is_api') or e.get('is_json')]
    if not apis:
        continue
    container = svc['container']
    base_port = apis[0].get('port', '?')
    print(f'{container} (:{base_port})')
    print(f'  Endpoints: {len(apis)}')
    print(f'  Category: {svc.get(\"category\", \"other\")}')
    print()
"
```

## Step 2: Make API Calls

### Generic API Helper

Use this pattern for any service API call. It handles auth automatically:

```bash
python3 << 'PYEOF'
import requests, json, os

# Load credentials
creds = {}
if os.path.exists('/opt/agentharness/.env'):
    for line in open('/opt/agentharness/.env'):
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            creds[k.strip()] = v.strip()

# --- Configure for the target service ---
SERVICE = "SERVICE_NAME"     # e.g., "portainer", "sonarr"
BASE_URL = "http://localhost:PORT"
METHOD = "GET"               # GET, POST, PUT, DELETE
ENDPOINT = "/api/endpoint"
PAYLOAD = None               # dict for POST/PUT, None for GET

# --- Auth patterns per service type ---
headers = {"Content-Type": "application/json"}

# Portainer
if SERVICE == "portainer":
    headers["X-API-Key"] = creds.get("PORTAINER_API_KEY", "")

# Arr stack (Sonarr, Radarr, Prowlarr, Lidarr, Readarr)
elif SERVICE in ("sonarr", "radarr", "prowlarr", "lidarr", "readarr"):
    api_key = creds.get(f"{SERVICE.upper()}_API_KEY", "")
    ENDPOINT += f"{'&' if '?' in ENDPOINT else '?'}apikey={api_key}"

# Grafana
elif SERVICE == "grafana":
    headers["Authorization"] = f"Bearer {creds.get('GRAFANA_API_KEY', '')}"

# n8n
elif SERVICE == "n8n":
    headers["X-N8N-API-KEY"] = creds.get("N8N_API_KEY", "")

# Jellyfin
elif SERVICE == "jellyfin":
    headers["X-Emby-Token"] = creds.get("JELLYFIN_API_KEY", "")

# Immich
elif SERVICE == "immich":
    headers["x-api-key"] = creds.get("IMMICH_API_KEY", "")

# Nextcloud
elif SERVICE == "nextcloud":
    headers["OCS-APIRequest"] = "true"
    nc_user = creds.get("NEXTCLOUD_USER", "admin")
    nc_pass = creds.get("NEXTCLOUD_PASSWORD", "")
    # Use basic auth
    from requests.auth import HTTPBasicAuth
    auth = HTTPBasicAuth(nc_user, nc_pass)

# NPM (Nginx Proxy Manager)
elif SERVICE == "npm":
    # NPM needs a token from login
    login = requests.post(f"{BASE_URL}/api/tokens", json={
        "identity": creds.get("NPM_EMAIL", ""),
        "secret": creds.get("NPM_PASSWORD", "")
    })
    if login.ok:
        headers["Authorization"] = f"Bearer {login.json().get('token', '')}"

# Pi-hole (no auth for read, admin token for write)
elif SERVICE == "pihole":
    pihole_token = creds.get("PIHOLE_API_TOKEN", "")
    if pihole_token:
        ENDPOINT += f"{'&' if '?' in ENDPOINT else '?'}auth={pihole_token}"

# Default: no auth (SearXNG, public endpoints)
# Just use headers as-is

# --- Make the request ---
url = f"{BASE_URL}{ENDPOINT}"
try:
    if METHOD == "GET":
        r = requests.get(url, headers=headers, timeout=15)
    elif METHOD == "POST":
        r = requests.post(url, headers=headers, json=PAYLOAD, timeout=15)
    elif METHOD == "PUT":
        r = requests.put(url, headers=headers, json=PAYLOAD, timeout=15)
    elif METHOD == "DELETE":
        r = requests.delete(url, headers=headers, timeout=15)

    if r.ok:
        try:
            data = r.json()
            print(json.dumps(data, indent=2)[:3000])
        except:
            print(r.text[:1000])
    else:
        print(f"Error {r.status_code}: {r.text[:500]}")
except Exception as e:
    print(f"Request failed: {e}")
PYEOF
```

## Service-Specific Operations

### Portainer — Container Management

```bash
# List all containers
curl -sf http://localhost:9000/api/endpoints/1/docker/containers/json?all=true \
  -H "X-API-Key: ${PORTAINER_API_KEY}" | python3 -m json.tool

# Restart a container
curl -sf -X POST http://localhost:9000/api/endpoints/1/docker/containers/CONTAINER_ID/restart \
  -H "X-API-Key: ${PORTAINER_API_KEY}"

# Get container stats
curl -sf http://localhost:9000/api/endpoints/1/docker/containers/CONTAINER_ID/stats?stream=false \
  -H "X-API-Key: ${PORTAINER_API_KEY}"
```

### Sonarr/Radarr — Media Management

```bash
# Sonarr: search for a show
curl -sf "http://localhost:8989/api/v3/series/lookup?term=SHOW_NAME&apikey=${SONARR_API_KEY}"

# Radarr: search for a movie
curl -sf "http://localhost:7878/api/v3/movie/lookup?term=MOVIE_NAME&apikey=${RADARR_API_KEY}"

# Sonarr: get calendar (upcoming episodes)
curl -sf "http://localhost:8989/api/v3/calendar?apikey=${SONARR_API_KEY}"

# Radarr: get queue (downloading)
curl -sf "http://localhost:7878/api/v3/queue?apikey=${RADARR_API_KEY}"
```

### Pi-hole — DNS Management

```bash
# Get summary stats
curl -sf "http://localhost/admin/api.php?summary"

# Get top blocked domains
curl -sf "http://localhost/admin/api.php?topItems=10&auth=${PIHOLE_API_TOKEN}"

# Disable blocking for 5 minutes
curl -sf "http://localhost/admin/api.php?disable=300&auth=${PIHOLE_API_TOKEN}"

# Enable blocking
curl -sf "http://localhost/admin/api.php?enable&auth=${PIHOLE_API_TOKEN}"
```

### Jellyfin — Media Server

```bash
# Get active sessions (who's watching)
curl -sf "http://localhost:8096/Sessions" -H "X-Emby-Token: ${JELLYFIN_API_KEY}"

# Get libraries
curl -sf "http://localhost:8096/Library/VirtualFolders" -H "X-Emby-Token: ${JELLYFIN_API_KEY}"

# Search media
curl -sf "http://localhost:8096/Items?searchTerm=QUERY&Recursive=true&Limit=10" \
  -H "X-Emby-Token: ${JELLYFIN_API_KEY}"
```

### Immich — Photo Management

```bash
# Server info
curl -sf "http://localhost:2283/api/server-info/statistics" -H "x-api-key: ${IMMICH_API_KEY}"

# Search photos
curl -sf -X POST "http://localhost:2283/api/search/smart" \
  -H "x-api-key: ${IMMICH_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "SEARCH_TERM"}'

# Get recent uploads
curl -sf "http://localhost:2283/api/timeline/buckets" -H "x-api-key: ${IMMICH_API_KEY}"
```

### Nextcloud — Files & Storage

```bash
# Get storage info
curl -sf "http://localhost:8080/ocs/v2.php/cloud/users/USER?format=json" \
  -u "USER:PASSWORD" -H "OCS-APIRequest: true"

# List files in a directory
curl -sf -X PROPFIND "http://localhost:8080/remote.php/dav/files/USER/PATH/" \
  -u "USER:PASSWORD"

# Share a file
curl -sf -X POST "http://localhost:8080/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json" \
  -u "USER:PASSWORD" -H "OCS-APIRequest: true" \
  -d "path=/PATH&shareType=3&permissions=1"
```

### NPM — Reverse Proxy

```bash
# List all proxy hosts
TOKEN=$(curl -sf http://localhost:81/api/tokens -d '{"identity":"EMAIL","secret":"PASSWORD"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -sf http://localhost:81/api/nginx/proxy-hosts -H "Authorization: Bearer ${TOKEN}"

# Create a new proxy host
curl -sf -X POST http://localhost:81/api/nginx/proxy-hosts \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "domain_names": ["service.yourdomain.com"],
    "forward_host": "container_name",
    "forward_port": 8080,
    "forward_scheme": "http",
    "access_list_id": 0,
    "certificate_id": 0,
    "ssl_forced": false,
    "block_exploits": true,
    "advanced_config": ""
  }'
```

## Step 3: Credential Discovery

If a service API returns 401/403, the credential might already be on the system. Check:

```bash
# Check existing .env files for the service's API key
grep -ri "SERVICE_NAME\|API_KEY\|TOKEN\|PASSWORD" /opt/SERVICE_NAME/.env 2>/dev/null
grep -ri "SERVICE_NAME" /opt/agentharness/.env 2>/dev/null

# Check Docker container env vars
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' CONTAINER_NAME 2>/dev/null | grep -i "key\|token\|pass\|secret"
```

If found, add it to `/opt/agentharness/.env` for future use:

```bash
echo "SERVICE_API_KEY=discovered_value" >> /opt/agentharness/.env
chmod 600 /opt/agentharness/.env
```

## Step 4: When New Services Are Added

After any new container is deployed:

1. **service_registry.sh** probes its API endpoints automatically
2. Check if it needs an API key — look in its `.env` or container env vars
3. Add discovered credentials to `/opt/agentharness/.env`
4. The generic API helper above will then handle auth automatically

## MCP Note

OpenClaw does not natively support MCP yet. All service interaction goes through:
- `exec` tool → curl/docker commands (what this skill teaches)
- `web_fetch` tool → HTTP requests (simpler but less control)

When OpenClaw adds MCP support, these curl-based patterns can be migrated to MCP server connections. The service registry already catalogs MCP-compatible endpoints.
