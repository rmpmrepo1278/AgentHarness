#!/usr/bin/env bash
# =============================================================================
# post_compose_change.sh — Propagate compose changes to all dependent systems.
#
# Run this after ANY docker compose up/down/upsert that adds or removes services.
# It ensures the full stack stays consistent:
#   1. Regenerate containers.json (from live docker ps — so removals reflect immediately)
#   2. Run sync-registry.py (reconciles compose files ↔ service registry)
#   3. Prune dangling Docker volumes left by removed services
#   4. Reload Traefik (picks up new/removed dynamic route configs)
#   5. Clean excluded_containers.json (drop entries for removed containers)
#
# Idempotent and safe to call repeatedly.
# =============================================================================
set -uo pipefail

LOG=/home/rohit/agentharness/logs/post_compose_change.log
stamp() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(stamp)] $*" >> "$LOG"; }

log "=== post_compose_change START ==="

# --- 1. Refresh containers.json so health_monitor / auto_fix see the new state ---
log "Refreshing containers.json..."
python3 /home/rohit/.hermes/scripts/health_dashboard.py >> "$LOG" 2>&1 || log "health_dashboard.py failed (non-critical)"

# --- 2. Sync the service registry against compose files ---
log "Syncing service registry..."
python3 /home/rohit/agentharness/scripts/sync-registry.py >> "$LOG" 2>&1 || log "sync-registry.py failed (non-critical)"

# --- 3. Prune dangling volumes (orphaned by removed services) ---
log "Pruning dangling Docker volumes..."
DANGLING=$(docker volume ls -qf dangling=true 2>/dev/null)
if [ -n "$DANGLING" ]; then
    docker volume prune -f >> "$LOG" 2>&1 || log "docker volume prune failed (non-critical)"
    log "Pruned $(echo "$DANGLING" | wc -l) dangling volume(s)"
else
    log "No dangling volumes."
fi

# --- 4. Reload Traefik to pick up new/removed dynamic route configs ---
log "Reloading Traefik..."
TRAEFIK_CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i traefik | head -1)
if [ -n "$TRAEFIK_CONTAINER" ]; then
    # Prefer the Traefik API; fall back to SIGHUP
    if curl -sf --max-time 5 "http://127.0.0.1:8080/api/http/servers" >/dev/null 2>&1; then
        curl -sf --max-time 5 -X POST "http://127.0.0.1:8080/api/http/servers/local" >/dev/null 2>&1 || log "Traefik API reload failed, trying SIGHUP"
    fi
    docker exec "$TRAEFIK_CONTAINER" kill -HUP 1 >> "$LOG" 2>&1 || log "Traefik SIGHUP failed"
    log "Traefik reloaded ($TRAEFIK_CONTAINER)"
else
    log "Traefik not running — skipped reload"
fi

# --- 5. Clean excluded_containers.json (remove entries for containers not currently running) ---
log "Cleaning excluded_containers.json..."
python3 - <<'PYEOF' >> "$LOG" 2>&1 || true
import json, pathlib, subprocess
p = pathlib.Path("/home/rohit/.hermes/data/excluded_containers.json")
if not p.exists():
    raise SystemExit(0)
existing = json.load(open(p))
running = set()
try:
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=15)
    running = set(out.stdout.strip().split("\n"))
except Exception:
    pass
# keep excluded entries that are still running OR known infrastructure; otherwise prune
pruned = [name for name in existing if name in running or name in ("calibre-web",)]
if len(pruned) != len(existing):
    p.write_text(json.dumps(pruned, indent=2) + "\n")
    print(f"excluded_containers: {len(existing)} -> {len(pruned)} entries")
PYEOF

# --- 6. Trigger consolidated_health to run a check cycle ---
log "Triggering consolidated_health check..."
bash /home/rohit/agentharness/scripts/consolidated_health.sh >> "$LOG" 2>&1 || true

log "=== post_compose_change DONE ==="
