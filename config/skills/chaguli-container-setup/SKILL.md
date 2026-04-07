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
2. **What port?** Check if the requested port is available. If not, find the next free one:

```bash
REQUESTED_PORT=8083  # Replace with what the user asked for

# Check if port is in use
if ss -tlnp 2>/dev/null | grep -q ":${REQUESTED_PORT} " || \
   docker ps --format "{{.Ports}}" 2>/dev/null | grep -q "0.0.0.0:${REQUESTED_PORT}->"; then
    echo "PORT ${REQUESTED_PORT} IS ALREADY IN USE BY:"
    ss -tlnp 2>/dev/null | grep ":${REQUESTED_PORT} " | awk '{print $NF}'
    docker ps --format "{{.Names}}: {{.Ports}}" 2>/dev/null | grep ":${REQUESTED_PORT}->"
    echo ""

    # Find next available port starting from requested
    NEXT_FREE=${REQUESTED_PORT}
    while ss -tlnp 2>/dev/null | grep -q ":${NEXT_FREE} " || \
          docker ps --format "{{.Ports}}" 2>/dev/null | grep -q "0.0.0.0:${NEXT_FREE}->"; do
        NEXT_FREE=$((NEXT_FREE + 1))
    done
    echo "SUGGESTED ALTERNATIVE: ${NEXT_FREE}"
    echo ""
    echo "Also available nearby:"
    COUNT=0
    for p in $(seq $((REQUESTED_PORT)) $((REQUESTED_PORT + 20))); do
        if ! ss -tlnp 2>/dev/null | grep -q ":${p} " && \
           ! docker ps --format "{{.Ports}}" 2>/dev/null | grep -q "0.0.0.0:${p}->"; then
            echo "  :${p} — free"
            COUNT=$((COUNT + 1))
            [ ${COUNT} -ge 5 ] && break
        fi
    done
else
    echo "PORT ${REQUESTED_PORT} IS AVAILABLE"
fi
```

**IMPORTANT**: If the requested port is taken, ALWAYS tell the user and suggest the alternative. NEVER silently pick a different port. Ask: "Port 8083 is used by Jellyfin. 8084 is free — want me to use that instead?"

Full port map for reference:

```bash
echo "=== All ports in use ==="
(docker ps --format "{{.Names}}|{{.Ports}}" 2>/dev/null | while IFS='|' read -r name ports; do
    echo "$ports" | grep -oP '0\.0\.0\.0:\K\d+' | while read -r p; do
        printf "  :%s — %s (docker)\n" "$p" "$name"
    done
done; ss -tlnp 2>/dev/null | awk '/LISTEN/ {split($4,a,":"); split($NF,b,"\""); printf "  :%s — %s (system)\n", a[length(a)], b[2]}') | sort -t: -k2 -n | uniq
```

3. **Volume storage location** — determine where data should go based on service type and available space:

```bash
echo "=== Storage Map ==="
echo ""

# Available drives
echo "DRIVES:"
df -h / | awk 'NR==2 {printf "  SSD (/):     %s free / %s total (%s used)\n", $4, $2, $5}'
if [ -f /opt/agentharness/storage_paths.env ]; then
    source /opt/agentharness/storage_paths.env
    [ -n "${BACKUP_DRIVE:-}" ] && [ -d "${BACKUP_DRIVE}" ] && \
        df -h "${BACKUP_DRIVE}" | awk 'NR==2 {printf "  USB (%s): %s free / %s total\n", "'${BACKUP_DRIVE}'", $4, $2}'
fi
# Check for any other large mounts
df -h 2>/dev/null | awk 'NR>1 && $2 ~ /[TG]/ && $6 !~ /^\/boot|^\/snap|^\/$/ {printf "  %s (%s): %s free / %s total\n", $6, $1, $4, $2}'
echo ""

# Existing data directories (discover what's already in use)
echo "EXISTING SERVICE DATA:"
for d in /opt/*/; do
    [ -d "$d" ] || continue
    name=$(basename "$d")
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    has_compose=$([ -f "${d}docker-compose.yml" ] || [ -f "${d}compose.yml" ] && echo " [compose]" || echo "")
    echo "  /opt/${name}: ${size}${has_compose}"
done
echo ""

# Shared directories (media, downloads — used by multiple services)
echo "SHARED DIRECTORIES:"
for shared in /media /downloads /data /srv /mnt/media /opt/media; do
    [ -d "${shared}" ] && echo "  ${shared}: $(du -sh "${shared}" 2>/dev/null | cut -f1)" || true
done
# Also check what containers currently mount
echo ""
echo "VOLUME MOUNTS IN USE:"
docker ps --format "{{.Names}}" 2>/dev/null | while read -r c; do
    docker inspect --format "{{.Name}}: {{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}" "$c" 2>/dev/null
done | grep -v "^$" | sed 's|^/||' | head -20
```

### Storage Decision Rules

Based on the output above, decide where to put data:

**Config-only services** (pihole, homarr, npm, portainer):
- Small data. Always on SSD: `/opt/SERVICE/config`
- Typically < 500MB

**Media-heavy services** (jellyfin, immich, nextcloud, stump):
- Large data. Check if a shared media directory exists.
- If `/media` or `/srv/media` exists and is on a large drive, USE IT — don't create a new path
- If no shared media dir exists, use the largest available drive
- NEVER put 100GB+ media on the 256GB SSD

**Download services** (arr stack, transmission, qbittorrent):
- Check for existing `/downloads` directory
- Must be accessible by both the download client AND the media server
- Same filesystem as the media library (avoids slow cross-device copies)

**Database-backed services** (nextcloud, immich, gitea):
- Database on SSD for speed: `/opt/SERVICE/db`
- User data on larger drive: `/media/SERVICE/` or `/opt/SERVICE/data`

**Shared volume detection** — CRITICAL:

```bash
# Find which services share volumes (e.g., arr stack + jellyfin sharing /media)
echo "=== Shared Mounts ==="
docker inspect $(docker ps -q) 2>/dev/null | python3 -c "
import sys, json
mounts = {}
containers = json.load(sys.stdin)
for c in containers:
    name = c['Name'].strip('/')
    for m in c.get('Mounts', []):
        src = m.get('Source', '')
        if src and not src.startswith('/var/lib/docker'):
            mounts.setdefault(src, []).append(name)

for src, users in sorted(mounts.items()):
    if len(users) > 1:
        print(f'  {src} — shared by: {', '.join(users)}')
" 2>/dev/null
```

**IMPORTANT**: If the new service needs access to media/downloads that other services already use:
1. Find the existing shared path
2. Mount the SAME path — don't create a duplicate
3. Tell the user: "Your arr stack uses /media for downloads. I'll mount the same path in the new container."

**IMPORTANT**: If SSD free space is below 20GB, warn:
> "SSD has only Xgb free. This service's data should go on the USB drive instead. Config will stay on SSD for speed."

**IMPORTANT**: Always ask before creating new top-level directories. Show what you plan:
> "I'll create:
>   /opt/calibre/config (SSD, ~50MB)
>   /opt/calibre/library → /media/books (USB drive, for the actual library)
> Sound good?"

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
