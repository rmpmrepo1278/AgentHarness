---
name: chaguli-container-setup
description: Set up new Docker containers end-to-end — compose file, networking, reverse proxy, DNS, volumes, and wiring
requires:
  binaries: ["docker", "curl"]
---

# Container Setup

When Rohit asks to set up a new service, deploy a container, or says "install X" — follow this complete workflow.

## Step 1: Gather Information

Before doing anything, determine:

1. **What service?** (name, Docker image)
2. **What port?** Check what's already in use:

```bash
echo "=== Ports in use ==="
docker ps --format "{{.Names}}: {{.Ports}}" | sort
echo ""
echo "=== System ports ==="
ss -tlnp | grep LISTEN | awk '{print $4}' | sort -t: -k2 -n | tail -20
```

3. **Volume storage location** — check available space:

```bash
echo "=== Disk space ==="
df -h / | awk 'NR==2 {print "Root: " $4 " free"}'
if [ -f /opt/agentharness/storage_paths.env ]; then
    source /opt/agentharness/storage_paths.env
    [ -n "${BACKUP_DRIVE:-}" ] && df -h "${BACKUP_DRIVE}" | awk 'NR==2 {print "USB: " $4 " free"}'
fi
echo ""
echo "=== Existing volume locations ==="
docker volume ls --format "{{.Name}}" | head -10
ls -d /opt/*/data 2>/dev/null || true
```

4. **Docker network** — find which network other services use:

```bash
docker network ls --format "{{.Name}}" | grep -v "bridge\|host\|none"
```

## Step 2: Search for Setup Instructions

If unsure about the image or configuration:

```bash
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
curl -sf "${SEARXNG_URL}/search?q=SERVICE_NAME+docker+compose+self+hosted&format=json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('results', [])[:3]:
    print(f'• {r.get(\"title\", \"\")} — {r.get(\"url\", \"\")}')
"
```

Also check Docker Hub for the official image:

```bash
curl -sf "https://hub.docker.com/v2/repositories/library/SERVICE_NAME/" 2>/dev/null || \
curl -sf "https://hub.docker.com/v2/repositories/linuxserver/SERVICE_NAME/" 2>/dev/null | \
python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('description','')[:200])"
```

## Step 3: Create the Service Directory

```bash
SERVICE_NAME="the-service"
SERVICE_DIR="/opt/${SERVICE_NAME}"
mkdir -p "${SERVICE_DIR}"
cd "${SERVICE_DIR}"
```

## Step 4: Generate docker-compose.yml

Create the compose file. Always include:
- `restart: unless-stopped`
- Named volume or bind mount (prefer `/opt/SERVICE/data`)
- Connect to the homelab Docker network
- Health check if the image supports it
- Environment variables from `.env` file

```bash
cat > docker-compose.yml << 'COMPOSE'
version: '3.8'

services:
  SERVICE_NAME:
    image: IMAGE_NAME:latest
    container_name: SERVICE_NAME
    restart: unless-stopped
    ports:
      - "HOST_PORT:CONTAINER_PORT"
    volumes:
      - ./data:/DATA_PATH
    environment:
      - TZ=America/Los_Angeles
    networks:
      - homelab

networks:
  homelab:
    external: true
COMPOSE
```

If the service needs a `.env` file:

```bash
cat > .env << 'ENV'
# SERVICE_NAME configuration
TZ=America/Los_Angeles
# Add service-specific vars here
ENV
chmod 600 .env
```

## Step 5: Start the Container

```bash
cd /opt/SERVICE_NAME
docker compose up -d
```

Verify it's running:

```bash
sleep 5
docker ps --filter "name=SERVICE_NAME" --format "{{.Names}}: {{.Status}}"
```

Check logs for errors:

```bash
docker logs --tail 20 SERVICE_NAME
```

## Step 6: Wire Into the Homelab

### 6a. Reverse Proxy (NPM)

If the service needs external/subdomain access, add a proxy host in Nginx Proxy Manager:

```bash
echo "NPM is at: $(docker port npm 2>/dev/null || docker port nginx-proxy-manager 2>/dev/null || echo 'check manually')"
echo ""
echo "Add a proxy host:"
echo "  Domain: SERVICE_NAME.yourdomain.com (or local hostname)"
echo "  Forward: SERVICE_NAME:CONTAINER_PORT"
echo "  SSL: Request Let's Encrypt cert (if external)"
```

Note: NPM API may require authentication. If Chaguli has the NPM skill, use it to add the proxy host programmatically.

### 6b. DNS (Pi-hole)

If using local DNS for the service:

```bash
echo "Add local DNS in Pi-hole:"
echo "  SERVICE_NAME.local -> $(hostname -I | awk '{print $1}')"
echo ""
echo "Pi-hole admin: http://localhost/admin"
```

### 6c. Homarr Dashboard

If you want the service on the dashboard:

```bash
echo "Add to Homarr: http://localhost:HOMARR_PORT"
echo "  Name: SERVICE_NAME"
echo "  URL: http://$(hostname -I | awk '{print $1}'):HOST_PORT"
echo "  Icon: search for SERVICE_NAME icon"
```

## Step 7: Wire Into Chaguli

Refresh the service registry and sync skills:

```bash
bash /opt/agentharness/scripts/service_registry.sh
bash /opt/agentharness/scripts/openclaw_sync.sh
```

This auto-generates a SKILL.md for the new service and updates AGENTS.md.

## Step 8: Verify End-to-End

```bash
echo "=== Container ==="
docker ps --filter "name=SERVICE_NAME"
echo ""
echo "=== Health ==="
curl -sf http://localhost:HOST_PORT/ && echo "Responding" || echo "Not responding yet"
echo ""
echo "=== Skill generated? ==="
ls ~/.openclaw/workspace/skills/homelab-SERVICE_NAME/SKILL.md 2>/dev/null && echo "Yes" || echo "Not yet — run openclaw_sync.sh"
```

## Common Service Templates

### Media server (Jellyfin-like)
- Ports: 8096 (web), 8920 (HTTPS)
- Volumes: /opt/SERVICE/config, /media (shared media library)
- Network: homelab

### *arr stack service
- Ports: varies (7878 radarr, 8989 sonarr, etc.)
- Volumes: /opt/SERVICE/config, /downloads, /media
- Network: homelab
- Needs: API key in .env

### Database-backed app
- Two containers: app + db (postgres/mariadb)
- Volumes: db data in named volume
- Network: homelab (app talks to db via container name)

### Static site / dashboard
- Single container, single port
- Volume: config only
- Network: homelab

## Safety Rules

1. ALWAYS check if the port is already in use before exposing
2. ALWAYS use `restart: unless-stopped` (not `always` — allows manual stops)
3. ALWAYS put data volumes under /opt/SERVICE/ (not random locations)
4. ALWAYS set file permissions on .env to 600
5. NEVER use `--privileged` unless absolutely required
6. NEVER expose management ports (databases, admin panels) to 0.0.0.0 — use 127.0.0.1
7. ALWAYS connect to the homelab Docker network (not bridge)
8. ALWAYS run service_registry.sh + openclaw_sync.sh after setup
