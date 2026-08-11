#!/bin/bash
# Encrypted off-site mirror of the homelab BRAIN (memory, configs).
# Writes through rclone crypt remote "braincrypt:" -> gdrive-backup:homelab-backup/brain-crypt
# Client-side encryption means GDrive never stores readable plaintext.
# Run nightly by backup_all.sh. Failures are logged, never abort the chain.
set -uo pipefail

LOG=/home/rohit/agentharness/logs/brain_sync.log
REMOTE=braincrypt:
STAMP=$(date "+%Y-%m-%d %H:%M:%S")
FAIL=0
RCLONE=(rclone copy --transfers 4 --checkers 8 --stats-one-line -q)

log() { echo "[$STAMP] $*" >> "$LOG"; }

mkdir -p "$(dirname "$LOG")"

step() {
  local name=$1; shift
  "${RCLONE[@]}" "$@" >> "$LOG" 2>&1 || { log "$name FAIL"; FAIL=1; }
}

# .hermes = agent memory/state (exclude reinstallable hermes-agent, bins, logs). ~349M.
step hermes --exclude "hermes-agent/**" --exclude "bin/**" --exclude "logs/**" \
  /home/rohit/.hermes "$REMOTE/hermes/"

# Compose + router configs (tiny).
step compose /home/rohit/services/docker/compose "$REMOTE/compose/"
step traefik --exclude "certs/accounts/**" \
  /home/rohit/services/traefik "$REMOTE/traefik/"

# Agentharness = MCP servers + orchestrator scripts (exclude venv + heavy mcp codebase).
step agentharness --exclude "venv/**" --exclude "codebase-memory-mcp/**" \
  /home/rohit/agentharness "$REMOTE/agentharness/"

# Synapse memory DB (named volume). sudo rclone must use rohit's config for the crypt remote.
sudo rclone copy --config /home/rohit/.config/rclone/rclone.conf --transfers 2 -q \
  /var/lib/docker/volumes/agentharness_synapse_data/_data \
  "$REMOTE/synapse/" >> "$LOG" 2>&1 || { log "synapse FAIL"; FAIL=1; }

log "brain sync done (failures: $FAIL)"
exit 0