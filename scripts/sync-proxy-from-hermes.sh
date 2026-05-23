#!/usr/bin/env bash
# Ensure homelab proxy runtime uses symlinks to hermes-agent source of truth.
set -euo pipefail

SRC="/home/rohit/.hermes/hermes-agent/proxy/core/providers"
DST="/home/rohit/agentharness/core/providers"
CG_REPO="/home/rohit/.hermes/hermes-agent/costguard"
CG_LIVE="/home/rohit/.hermes/lib/costguard"

install_symlink() {
  local link="$1" target="$2"
  rm -f "$link"
  ln -s "$target" "$link"
  echo "linked $(basename "$link")"
}

for f in proxy_server.py router.py anthropic_compat.py; do
  install_symlink "$DST/$f" "$SRC/$f"
done

# CostGuard: repo copy is canonical; live path is a symlink for backward compat
if [ ! -d "$CG_REPO" ]; then
  echo "ERROR: missing costguard repo at $CG_REPO" >&2
  exit 1
fi
rm -f "$CG_LIVE"
ln -s "$CG_REPO" "$CG_LIVE"
echo "linked ~/.hermes/lib/costguard"

mkdir -p /home/rohit/agentharness/data
rm -rf /home/rohit/agentharness/data/costguard-lib
ln -s "$CG_LIVE" /home/rohit/agentharness/data/costguard-lib
echo "linked agentharness/data/costguard-lib"

rm -f "$DST"/__pycache__/proxy_server*.pyc "$DST"/__pycache__/anthropic_compat*.pyc 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/verify-proxy-sync.sh"
echo "Done. Restart: sudo systemctl restart agentharness-llm-proxy.service"
