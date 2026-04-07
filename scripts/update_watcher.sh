#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# update_watcher.sh — Track versions of running containers, alert on updates
#
# Checks Docker Hub / GitHub for newer versions of running images.
# Prioritizes security updates. Runs weekly during online hours.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

VERSIONS_FILE="/opt/agentharness/container_versions.json"
UPDATE_REPORT="/opt/agentharness/reports/updates_$(timestamp).md"

main() {
    log_header "Update Watcher"
    ensure_dir /opt/agentharness/reports

    cat > "${UPDATE_REPORT}" << EOF
# Container Update Report
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    # Get current container images and versions
    python3 << 'PYEOF' >> "${UPDATE_REPORT}"
import json, subprocess, re
from datetime import datetime

containers = {}
result = subprocess.run(
    ["docker", "ps", "--format", "{{.Names}}|{{.Image}}"],
    capture_output=True, text=True, timeout=30
)

for line in result.stdout.strip().split("\n"):
    if "|" not in line:
        continue
    name, image = line.split("|", 1)

    # Parse image name and tag
    if ":" in image:
        img_name, tag = image.rsplit(":", 1)
    else:
        img_name, tag = image, "latest"

    # Get image creation date
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.Created}}", image],
        capture_output=True, text=True, timeout=10
    )
    created = inspect.stdout.strip()[:19] if inspect.returncode == 0 else "unknown"

    containers[name] = {
        "image": image,
        "image_name": img_name,
        "tag": tag,
        "local_created": created
    }

# Load previous versions for comparison
prev = {}
try:
    prev = json.load(open("/opt/agentharness/container_versions.json"))
except:
    pass

# Check for updates via Docker Hub API (rate-limited, best effort)
updates_available = []
for name, info in containers.items():
    img = info["image_name"]

    # Skip local/custom images
    if "/" not in img and img not in ("postgres", "redis", "nginx", "python", "node"):
        continue

    # Normalize for Docker Hub API
    if "/" not in img:
        img = f"library/{img}"

    try:
        # Check Docker Hub for latest tag
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "5",
             f"https://hub.docker.com/v2/repositories/{img}/tags/{info['tag']}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            remote_updated = data.get("last_updated", "")[:19]
            local_created = info.get("local_created", "")[:19]

            if remote_updated and local_created and remote_updated > local_created:
                updates_available.append({
                    "container": name,
                    "image": info["image"],
                    "local_date": local_created,
                    "remote_date": remote_updated
                })
    except:
        pass

# Save current state
json.dump(containers, open("/opt/agentharness/container_versions.json", "w"), indent=2)

# Report
print(f"## Current Images ({len(containers)})\n")
print(f"| Container | Image | Tag | Local Date |")
print(f"|-----------|-------|-----|------------|")
for name, info in sorted(containers.items()):
    print(f"| {name} | {info['image_name']} | {info['tag']} | {info['local_created'][:10]} |")

if updates_available:
    print(f"\n## Updates Available ({len(updates_available)})\n")
    for u in updates_available:
        print(f"- **{u['container']}** ({u['image']}): local {u['local_date'][:10]} → remote {u['remote_date'][:10]}")
    print(f"\nTo update: `docker compose pull && docker compose up -d` in the service directory")
else:
    print(f"\n## All Up to Date\n")
    print("No updates found for running containers.")
PYEOF

    # Alert if security-critical updates exist
    local update_count
    update_count=$(grep -c "Updates Available" "${UPDATE_REPORT}" 2>/dev/null || echo "0")
    if [ "${update_count}" -gt 0 ]; then
        bash "${SCRIPT_DIR}/monitor.sh" alert INFO "Container updates available. See ${UPDATE_REPORT}" || true
    fi

    log_ok "Report: ${UPDATE_REPORT}"
}

main "$@"
