#!/usr/bin/env bash
# Verify homelab LLM proxy runtime matches source-of-truth symlinks.
set -euo pipefail

SRC="/home/rohit/.hermes/hermes-agent/proxy/core/providers"
DST="/home/rohit/agentharness/core/providers"
FILES=(proxy_server.py router.py anthropic_compat.py)
FAIL=0

check_symlink() {
  local link="$1" target="$2"
  if [ ! -L "$link" ]; then
    echo "FAIL: $link is not a symlink (drift risk)"
    FAIL=1
    return
  fi
  local resolved
  resolved="$(readlink -f "$link")"
  if [ "$resolved" != "$target" ]; then
    echo "FAIL: $link -> $resolved (expected $target)"
    FAIL=1
    return
  fi
  echo "OK: $link"
}

for f in "${FILES[@]}"; do
  check_symlink "$DST/$f" "$SRC/$f"
done

# CostGuard canonical path
if [ ! -L /home/rohit/.hermes/lib/costguard ]; then
  echo "FAIL: ~/.hermes/lib/costguard should symlink to hermes-agent/costguard"
  FAIL=1
else
  echo "OK: ~/.hermes/lib/costguard symlink"
fi

if [ ! -L /home/rohit/agentharness/data/costguard-lib ]; then
  echo "FAIL: agentharness/data/costguard-lib should be a symlink"
  FAIL=1
else
  echo "OK: agentharness/data/costguard-lib symlink"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "Proxy sync verification FAILED"
  exit 1
fi

echo "Proxy sync verification passed"
