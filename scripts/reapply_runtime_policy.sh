#!/usr/bin/env bash
# reapply_runtime_policy.sh — idempotent re-assertion of container memory caps.
#
# Watchtower-replacement: `docker compose` ignores deploy.resources, so image
# upgrades / container recreates silently drop caps. The scheduler runs this
# each cycle (via autonomous_fixer) to detect and re-apply the canonical policy.
#
# Also re-adopts the ollama container under compose if it lost its project
# labels (health-check inventory matching depends on them).
set -u

# name:cap-bytes  (byte values are what `docker inspect .HostConfig.Memory` reports)
POLICY="
ollama:12884901888
authentik-server:1073741824
authentik-worker:536870912
authentik-db:536870912
authentik-redis:268435456
traefik:1073741824
linkwarden:1073741824
vaultwarden:1073741824
homepage:1073741824
paperless:2147483648
bookstack:1073741824
healthchecks:1073741824
searxng:1073741824
immich_server:2147483648
paperless-db:536870912
linkwarden-db:268435456
bookstack-db:268435456
pihole:1073741824
immich_database:2147483648
redis:1073741824
immich_machine_learning:2147483648
"

CHANGED=0
for line in $POLICY; do
    [ -z "$line" ] && continue
    name="${line%%:*}"
    want="${line#*:}"
    cid=$(docker ps -q -f "name=^${name}$" 2>/dev/null) || continue
    [ -z "$cid" ] && continue
    have=$(docker inspect -f '{{.HostConfig.Memory}}' "$cid" 2>/dev/null || echo 0)
    if [ "$have" != "$want" ]; then
        if docker update --memory "$want" "$cid" >/dev/null 2>&1; then
            echo "reapply: $name memory cap ${have:-0} -> $want"
            CHANGED=$((CHANGED+1))
        else
            echo "FAIL: could not reapply cap for $name"
        fi
    fi
done

# Ollama: keep it compose-managed so inventory/health-check can match it.
if OID=$(docker ps -q -f "name=^ollama$" 2>/dev/null); then
    if ! docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$OID" 2>/dev/null | grep -q "compose"; then
        if docker compose -f /home/rohit/services/docker/compose/apps.yml up -d --no-recreate ollama >/dev/null 2>&1; then
            echo "reapply: ollama re-adopted under compose (compose project label restored)"
            CHANGED=$((CHANGED+1))
        else
            echo "FAIL: could not re-adopt ollama under compose"
        fi
    fi
fi

if [ "$CHANGED" -gt 0 ]; then
    echo "runtime policy: re-applied to $CHANGED target(s)"
else
    echo "runtime policy: compliant (no changes)"
fi