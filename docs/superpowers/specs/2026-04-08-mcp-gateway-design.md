# MCP Gateway + Docker MCP — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Author:** Rohit + Claude

## Problem

Chaguli can understand requests like "deploy Paperless-ngx", "remove open-webui", and "check if F1 download finished" — but has no tools to execute them. Users repeat themselves and get no results. Adding capabilities today requires patching `tools.py` by hand.

## Solution

A persistent MCP gateway that sits between Chaguli and any number of MCP servers. MCP servers self-register on startup. The gateway manages health, auto-recovery, and tool routing. New capabilities arrive by starting a new MCP container — zero code changes to Chaguli.

The first MCP server is a Docker MCP that gives Chaguli full container management.

## Architecture

### Containers

Three containers on a shared Docker network (`chaguli-net`):

| Container | Port | Role | Docker Socket |
|-----------|------|------|---------------|
| `chaguli` | 8093 | Telegram bot + LLM agent | No |
| `mcp-gateway` | 8094 | MCP registry, health monitor, tool router, auto-recovery | Yes |
| `docker-mcp` | 8095 | Docker container/stack management | Yes |

### Request Flow

```
User (Telegram) → Chaguli
  → LLM returns tool_calls: [{name: "deploy_stack", args: {...}}]
  → Chaguli dispatch() → is it an MCP tool? → POST http://mcp-gateway:8094/tools/call
  → Gateway looks up owner → routes to http://docker-mcp:8095
  → Docker MCP executes docker compose up -d
  → Result flows back: Docker MCP → Gateway → Chaguli → Telegram
```

### Self-Registration

Every MCP server, on startup, registers with the gateway:

```
POST http://mcp-gateway:8094/register
{
  "name": "docker",
  "address": "http://docker-mcp:8095",
  "container_name": "docker-mcp",
  "tools": [...]   // optional — gateway will also call tools/list to verify
}
```

On shutdown (graceful):
```
POST http://mcp-gateway:8094/deregister
{"name": "docker"}
```

The gateway caches the tool catalog. Chaguli queries `GET /tools/catalog` to get all available tools in OpenAI function-calling format, which get passed to the LLM.

**Registration resilience:** MCP servers do not fire-and-forget the registration call. On startup, each MCP retries registration with exponential backoff (10s, 20s, 40s, 60s cap) until the gateway acknowledges. This handles the case where the gateway boots slower than the MCP (Docker `depends_on` only waits for container start, not readiness). The registration retry loop runs as a background thread so the MCP can accept health checks while waiting.

**Gateway restart recovery:** On startup, the gateway loads `gateway_state.json` and re-probes every persisted MCP before trusting the cached state. An MCP that was `healthy` when the gateway went down may be dead now — the gateway verifies with a live health check before marking it active. MCPs also re-register themselves if they detect the gateway was unreachable and came back (they heartbeat the gateway too, and re-register on reconnection).

### Health & Recovery

| Condition | Action |
|-----------|--------|
| MCP responds to health check | Status: `healthy` |
| 3 consecutive missed health checks (60s interval) | Status: `degraded`, tools still visible but marked flaky. Health check frequency increases to every 15s to detect recovery faster. |
| 5 consecutive missed health checks | Status: `offline`, gateway runs `docker restart <container>` |
| MCP comes back after restart | Self-registers again, status: `healthy`, tools re-appear, health check returns to 60s |
| MCP still failing after restart | Status: `failed`, tools hidden, Telegram notification to user |

No infinite restart loops. One automatic recovery attempt, then escalate.

**Adaptive health check frequency:**
- `healthy`: every 60s (normal)
- `degraded`: every 15s (detect recovery faster)
- `offline` (post-restart, waiting for recovery): every 10s for 2 minutes, then back to 60s
- `failed`: every 5 minutes (low-priority background check in case it comes back)

### Gateway Self-Monitoring

The gateway itself is a single point of failure. Three mitigations:

1. **Docker restart policy** `unless-stopped` — handles crashes.
2. **Chaguli watchdog** — Chaguli's existing heartbeat system monitors the gateway via `GET http://mcp-gateway:8094/status`. If unreachable for > 2 minutes, Chaguli runs `docker restart mcp-gateway` via a direct Docker API call (Chaguli gets minimal Docker socket access for this one operation only, or uses the host-level `docker` CLI via a mounted script).
3. **Chaguli catalog cache** — Chaguli caches the last-known tool catalog and MCP addresses locally. If the gateway is temporarily unreachable, Chaguli routes tool calls directly to the last-known MCP address. Degraded mode (no health checks, no routing intelligence), but not dead. Tools keep working while the gateway recovers.

### Tool Catalog Refresh

- On registration: gateway calls `tools/list` on the MCP, caches result
- On health check recovery: gateway re-fetches `tools/list` (tools may have changed)
- Chaguli fetches catalog once at startup and on a configurable interval (default: 60s)
- Gateway exposes `GET /tools/catalog` returning all tools from all healthy MCPs in OpenAI format

## Docker MCP — Tools

### Day-one tools

| Tool | Description | Args |
|------|-------------|------|
| `list_containers` | List all containers (running + stopped) | `filter` (optional): name pattern |
| `deploy_stack` | Deploy a compose stack | `name`: stack name, `compose_yaml`: raw YAML or `template`: template name, `vars`: template variables |
| `remove_container` | Stop and remove a container | `name`: container name, `remove_volumes`: bool (default false) |
| `container_logs` | Get recent logs | `name`: container name, `tail`: lines (default 50) |
| `container_status` | Inspect container (health, ports, mounts, uptime) | `name`: container name |
| `restart_container` | Restart a container | `name`: container name |

### Compose Templates

Templates are vetted docker-compose YAML files for common homelab apps. Two sources, merged at runtime:

1. **Repo templates** (read-only): `AgentHarness/templates/docker/` — shipped with the codebase, deployed via scp. Versioned.
2. **Local overrides** (read-write): `~/mcp-gateway/templates/` on the host — hotfix or add templates without a code push. Takes precedence over repo templates with the same name.

Template format:
```yaml
# templates/docker/paperless-ngx.yml
# vars: DATA_DIR, PORT, SECRET_KEY
version: "3.8"
services:
  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    ports:
      - "${PORT:-8010}:8000"
    volumes:
      - ${DATA_DIR:-/opt/paperless}/data:/usr/src/paperless/data
      - ${DATA_DIR:-/opt/paperless}/media:/usr/src/paperless/media
    environment:
      PAPERLESS_SECRET_KEY: ${SECRET_KEY:-changeme}
    restart: unless-stopped
```

### Deploy flow with approval

```
User: "Deploy Paperless-ngx"

1. Chaguli LLM → tool_call: deploy_stack(template: "paperless-ngx")
2. Docker MCP checks templates:
   a. Local override ~/mcp-gateway/templates/paperless-ngx.yml? Use it.
   b. Repo template templates/docker/paperless-ngx.yml? Use it.
   c. Neither? Return: "No template found. Generate one?"
3. If template found → deploy directly (vetted config)
4. If no template (LLM-generated YAML):
   → Docker MCP returns the YAML to Chaguli with require_approval: true
   → Chaguli shows you the compose on Telegram: "Here's what I'd deploy. Go ahead?"
   → You say "yes" → Chaguli calls deploy_stack again with approved YAML
   → You say "no" or suggest changes → Chaguli adjusts
```

Templated deploys skip approval. LLM-generated deploys always require approval.

### Automatic Port Allocation

When deploying a stack, the Docker MCP does not rely on the LLM to pick a host port. Instead:

1. Template specifies a default port (e.g., `${PORT:-8010}`)
2. Before deploying, Docker MCP checks if that port is in use (`ss -tlnp` or Docker API)
3. If occupied, auto-increments to the next free port in the range 8000-9000
4. Returns the actual assigned port in the deploy result so Chaguli can tell the user: "Paperless-ngx is up on port 8011" (not the default 8010 which was taken)

### Secrets Management

Templates often need secrets (DB passwords, API keys, encryption keys). Handled automatically:

1. **Auto-generation:** When a template variable looks like a secret (name contains `SECRET`, `PASSWORD`, `KEY`, `TOKEN`), and no value is provided, Docker MCP generates a random 32-character alphanumeric string.
2. **Persistence:** Generated secrets are saved to `~/mcp-gateway/secrets/<stack-name>.env`. This file survives container restarts and redeploys.
3. **Reuse on redeploy:** If a stack is redeployed, existing secrets from the persisted `.env` are reused — not regenerated. This prevents breaking a running app by changing its secret key.
4. **User override:** If the user provides a secret explicitly (via template vars), that takes precedence over auto-generation.

No secrets are logged, displayed in Telegram, or stored in gateway state.

### Resource Guard

Before any deploy, Docker MCP checks system resources:

```python
# Pre-deploy checks
available_mem_mb = get_free_memory_mb()
if available_mem_mb < 400:
    return {
        "status": "refused",
        "reason": f"Only {available_mem_mb}MB free. Remove a container or add swap first.",
        "suggestion": "Run /tasks to see what's running, or ask me to remove something."
    }
```

Thresholds (configurable via env vars):
- **Memory:** Refuse deploy if < 400MB free (default). Warn if < 800MB.
- **Disk:** Refuse if < 2GB free on the data partition. Warn if < 5GB.

### Post-Deploy Health Verification

After `deploy_stack` succeeds, Docker MCP doesn't just return "deployed". It:

1. Waits 10 seconds for the container to stabilize
2. Checks container status — is it running or crash-looping?
3. If crash-looping (restarted > 2 times in 30s):
   - Captures the last 20 lines of logs
   - Returns: `{"status": "deployed_but_failing", "logs": "...", "offer_rollback": true}`
   - Chaguli tells the user: "Paperless-ngx deployed but it's crash-looping. Logs: ... Want me to roll it back?"
4. If healthy: returns `{"status": "healthy", "ports": {"8010": "8000"}, "url": "http://192.168.29.10:8010"}`

### Template Hot-Reload

The Docker MCP watches both template directories for changes:

- Uses `inotify` (Linux) to detect new/modified `.yml` files
- On change: re-reads the template, updates the in-memory template index
- No restart required — drop a new template file, it's immediately available

This means: scp a new template from your work Mac, and Chaguli can use it on the next request.

## MCP Gateway — Components

### 1. Registry (`registry.py`)

In-memory dict of registered MCPs, persisted to `gateway_state.json` for restart recovery.

```python
{
  "docker": {
    "name": "docker",
    "address": "http://docker-mcp:8095",
    "container_name": "docker-mcp",  # for auto-restart
    "status": "healthy",             # healthy | degraded | offline | failed
    "registered_at": "2026-04-08T12:00:00",
    "last_health_check": "2026-04-08T15:30:00",
    "consecutive_failures": 0,
    "tools": [...]                   # cached tool catalog
  }
}
```

### 2. Health Monitor (`health.py`)

Background thread, 60s interval per MCP:
- Calls `POST /health` or MCP `initialize` as a ping
- Updates `consecutive_failures` counter
- Triggers recovery actions per the table above
- Uses Docker SDK to restart containers when needed

### 3. Tool Router (`router.py`)

- Maintains a name→MCP mapping from all registered tool catalogs
- On `POST /tools/call {name, arguments}`:
  1. Look up which MCP owns the tool
  2. If MCP is `healthy` or `degraded`: forward the call via JSON-RPC `tools/call`
  3. If MCP is `offline`/`failed`: return error "Tool unavailable, [MCP name] is down"
- Converts MCP tool schemas to OpenAI function-calling format for `GET /tools/catalog`

### 4. HTTP API (`server.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/register` | POST | MCP self-registration |
| `/deregister` | POST | MCP graceful shutdown |
| `/tools/catalog` | GET | All tools in OpenAI format (for Chaguli) |
| `/tools/call` | POST | Route a tool call to the right MCP |
| `/status` | GET | Gateway health + all MCP statuses |
| `/mcps` | GET | List registered MCPs |
| `/logs` | GET | Recent gateway activity log (last N events, filterable by event type) |

### 5. Rate Limiter (`rate_limiter.py`)

Protects against LLM hallucination loops (e.g., the LLM calling `deploy_stack` repeatedly).

- **Per-tool limits:** Configurable max calls per minute per tool (defaults below)
- **Global limit:** Max 30 tool calls per minute across all tools
- **Destructive tool limits** are tighter than read-only tools

| Tool | Default limit |
|------|--------------|
| `list_containers` | 10/min |
| `container_status` | 10/min |
| `container_logs` | 10/min |
| `deploy_stack` | 2/min |
| `remove_container` | 3/min |
| `restart_container` | 3/min |

When rate limited, gateway returns `{"error": "rate_limited", "retry_after_seconds": N}`. Chaguli tells the user: "I'm making too many requests. Cooling down."

### 6. Structured Logger (`gateway_log.py`)

All gateway activity logged to `/data/gateway.log` as JSON lines:

```json
{"ts": "2026-04-08T15:30:00", "event": "mcp_registered", "mcp": "docker", "tools": 6}
{"ts": "2026-04-08T15:31:00", "event": "health_check", "mcp": "docker", "status": "healthy"}
{"ts": "2026-04-08T15:32:10", "event": "tool_call", "tool": "list_containers", "mcp": "docker", "duration_ms": 120, "success": true}
{"ts": "2026-04-08T16:00:00", "event": "health_degraded", "mcp": "docker", "consecutive_failures": 3}
{"ts": "2026-04-08T16:02:00", "event": "auto_restart", "mcp": "docker", "container": "docker-mcp"}
{"ts": "2026-04-08T16:02:30", "event": "mcp_recovered", "mcp": "docker"}
```

Events logged: `mcp_registered`, `mcp_deregistered`, `health_check`, `health_degraded`, `health_offline`, `auto_restart`, `mcp_recovered`, `mcp_failed`, `tool_call`, `tool_error`, `rate_limited`, `catalog_refresh`, `gateway_started`, `gateway_shutdown`.

Log rotation: 7 days, max 50MB per file.

Chaguli can read this log to answer questions like "what happened with the gateway today?" via a `gateway_activity` tool exposed by the gateway itself.

### 7. Notification Bridge (`notify.py`)

When the gateway needs to tell the user something (MCP failed to recover, new MCP registered):
- Writes to Chaguli's `alerts_inbox.jsonl` (existing file-based communication)
- Chaguli's inbox watcher picks it up and sends via Telegram

## Chaguli Integration

### Tool dispatch bridge

Patch `agentharness_tools.py` (existing pattern) to:
1. On startup and every 60s: fetch `GET http://mcp-gateway:8094/tools/catalog`
2. Merge MCP tools into `TOOL_SCHEMAS` list
3. In `dispatch()`: if tool name is in MCP catalog, forward to gateway:
   ```python
   requests.post("http://mcp-gateway:8094/tools/call",
                  json={"name": tool_name, "arguments": tool_args})
   ```

This is a thin bridge — no MCP protocol knowledge in Chaguli. Gateway handles all MCP communication.

### No Chaguli code changes needed for new MCPs

When a new MCP registers (e.g., Paperless API MCP):
1. New MCP starts → registers with gateway
2. Gateway updates tool catalog
3. Next time Chaguli fetches catalog, new tools appear
4. LLM sees new tools, can call them
5. Chaguli's dispatch routes through gateway automatically

Zero patches. Zero restarts.

## Docker Setup

### docker-compose.yml (gateway + docker-mcp)

```yaml
version: "3.8"

networks:
  chaguli-net:
    external: true  # Chaguli container must also be on this network

services:
  mcp-gateway:
    build: ./mcp-gateway
    container_name: mcp-gateway
    ports:
      - "8094:8094"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./mcp-gateway/data:/data
      - ./templates/docker:/templates/repo:ro
      - ~/mcp-gateway/templates:/templates/local
    environment:
      - GATEWAY_PORT=8094
      - HEALTH_CHECK_INTERVAL=60
      - CHAGULI_ALERTS_DIR=/data/alerts
    networks:
      - chaguli-net
    restart: unless-stopped

  docker-mcp:
    build: ./docker-mcp
    container_name: docker-mcp
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./templates/docker:/templates/repo:ro
      - ~/mcp-gateway/templates:/templates/local
    environment:
      - MCP_PORT=8095
      - GATEWAY_URL=http://mcp-gateway:8094
    networks:
      - chaguli-net
    restart: unless-stopped
    depends_on:
      - mcp-gateway
```

### Network setup

The existing `chaguli` container needs to join `chaguli-net`:
```bash
docker network create chaguli-net
docker network connect chaguli-net chaguli
```

## File Structure

```
AgentHarness/
  mcp-gateway/
    Dockerfile
    server.py          # Flask HTTP API
    registry.py        # MCP registry + persistence
    health.py          # Health monitor + auto-recovery + adaptive frequency
    router.py          # Tool call routing
    rate_limiter.py    # Per-tool and global rate limiting
    gateway_log.py     # Structured JSON line logger
    notify.py          # Telegram notification bridge
    mcp_base.py        # Base class for MCP servers (registration retry, health endpoint)
    requirements.txt   # flask, docker, requests
    data/              # gateway_state.json, gateway.log (persisted)
  docker-mcp/
    Dockerfile
    server.py          # MCP server (JSON-RPC) — extends mcp_base
    tools.py           # Docker tool implementations
    templates.py       # Template resolution (repo + local) + hot-reload via inotify
    port_allocator.py  # Auto-detect free ports for deploys
    resource_guard.py  # Memory/disk checks before deploys
    secrets.py         # Auto-generate + persist secrets for templates
    requirements.txt   # docker, pyyaml, inotify
  templates/
    docker/
      paperless-ngx.yml
      uptime-kuma.yml
      jellyfin.yml
      ... (add as needed)
  scripts/
    setup_mcp_gateway.sh  # One-command bootstrap (see below)
```

## Bootstrap

First-time setup is a single script: `setup_mcp_gateway.sh`. Run once, never think about it again.

```bash
# On homelab:
bash ~/agentharness/scripts/setup_mcp_gateway.sh
```

The script does:

1. **Create Docker network** `chaguli-net` (idempotent — skips if exists)
2. **Connect Chaguli** to `chaguli-net` (idempotent — skips if already connected)
3. **Create local override directory** `~/mcp-gateway/templates/` and `~/mcp-gateway/secrets/`
4. **Build images** for `mcp-gateway` and `docker-mcp`
5. **Deploy** via `docker compose up -d`
6. **Wait for readiness** — polls `GET http://mcp-gateway:8094/status` until 200 (timeout 60s)
7. **Verify registration** — checks that docker-mcp appears in `GET /mcps`
8. **Patch Chaguli** — runs the existing `integrate_chaguli.sh` pattern to add the MCP bridge to `agentharness_tools.py`
9. **Restart Chaguli** — `docker restart chaguli`
10. **Smoke test** — calls `list_containers` through the gateway and verifies a result comes back

All steps are idempotent. Safe to re-run.

## MCP Base Class

All MCP servers share common behavior via `mcp_base.py` (shipped with the gateway, mounted into MCP containers):

```python
class MCPBase:
    """Base class for all MCP servers. Handles registration, health, and lifecycle."""

    def register_with_gateway(self, retries=True):
        """Register with gateway. Retries with backoff (10s, 20s, 40s, 60s cap) if gateway unreachable."""

    def deregister(self):
        """Graceful deregister on shutdown."""

    def health_endpoint(self):
        """GET /health — returns 200 with tool count and uptime."""

    def start(self, tools: list):
        """Start the MCP server: register, expose health endpoint, serve JSON-RPC."""
```

New MCP servers inherit this class. Registration retry, health endpoint, and graceful shutdown come for free. The MCP developer only implements the tools.

## Security

- Docker socket access is limited to the gateway and Docker MCP containers only
- Chaguli never touches Docker directly — always through the gateway (exception: gateway watchdog restart, one specific operation)
- LLM-generated compose YAML requires user approval before execution
- Template deploys are trusted (vetted configs)
- Gateway only accepts registration from containers on `chaguli-net`
- No external network exposure — all communication is container-to-container
- Secrets are auto-generated, persisted to host filesystem, never logged or displayed in Telegram
- Tool call rate limiting prevents LLM hallucination loops from causing damage

## Testing Plan

1. Deploy gateway + Docker MCP
2. Verify self-registration: `curl http://localhost:8094/mcps` shows docker-mcp
3. Verify tool catalog: `curl http://localhost:8094/tools/catalog` lists 6 tools
4. Test each tool via curl:
   - `list_containers` → see existing containers
   - `deploy_stack` with template → deploy Uptime Kuma
   - `container_status` → verify it's running
   - `container_logs` → see startup logs
   - `restart_container` → restart it
   - `remove_container` → clean up
5. Test recovery: `docker stop docker-mcp`, wait 5 minutes, verify auto-restart
6. Test Chaguli integration: send "list my containers" on Telegram
7. Test approval flow: "deploy something-without-a-template" → verify approval prompt

## Future MCPs

Once the gateway is running, adding new MCPs is just `docker run`:

| MCP | What it enables |
|-----|----------------|
| Paperless-ngx API MCP | Upload documents, search, tag from Telegram |
| Uptime Kuma MCP | Check service health, add monitors, get alerts |
| Home Assistant MCP | Control smart home devices |
| File Manager MCP | Download files, manage media library |
| Network MCP | Security scans, port checks, DNS management |
| Backup MCP | Trigger and monitor backups to 4TB USB |

Each one: build, add to compose, `docker compose up -d`. Gateway discovers it. Chaguli gains the tools.
