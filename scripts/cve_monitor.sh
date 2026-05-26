#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# cve_monitor.sh — Scan running Docker images for known CVEs
#
# Uses Trivy to scan images from running containers.
# Reports findings to JSON and alerts for critical/high CVEs.
# Schedule: Weekly via harness_registry.yaml
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REPORTS_DIR="${AH_REPORTS_DIR:-/home/rohit/agentharness/reports}"
DATA_DIR="${AH_DATA_DIR:-/home/rohit/agentharness/data}"
REPORT_FILE="${REPORTS_DIR}/cve_scan_$(date +%Y%m%d).json"
ALERT_FILE="${DATA_DIR}/cve_latest.json"
TMP_DIR=$(mktemp -d)

mkdir -p "${REPORTS_DIR}" "${DATA_DIR}"

log_info "Starting CVE scan of running container images..."

# Collect unique images from running containers
mapfile -t IMAGES < <(docker ps --format '{{.Image}}' | sort -u)
IMAGE_COUNT=${#IMAGES[@]}
log_info "Found ${IMAGE_COUNT} unique running images to scan"

# Check for trivy
if ! command -v trivy &>/dev/null; then
    log_warn "Trivy not found. Installing..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq 2>/dev/null
        sudo apt-get install -y -qq wget apt-transport-https gnupg lsb-release 2>/dev/null || true
        wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key 2>/dev/null | sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg 2>/dev/null || true
        echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee /etc/apt/sources.list.d/trivy.list >/dev/null
        sudo apt-get update -qq 2>/dev/null
        sudo apt-get install -y -qq trivy 2>/dev/null || true
    fi
fi

if ! command -v trivy &>/dev/null; then
    log_warn "Could not install trivy. Using docker image age as risk proxy."
    python3 << PYEOF
import json, subprocess, datetime

results = {"scan_date": datetime.datetime.now().isoformat(), "scanner": "age-proxy", "images": [], "alerts": []}

images_output = subprocess.check_output(["docker", "ps", "--format", "{{.Image}}"]).decode().strip()
images = sorted(set(filter(None, images_output.split("\n"))))

for img in images:
    try:
        inspect = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.Created}}", img],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        created = datetime.datetime.fromisoformat(inspect.replace("Z", "+00:00"))
        age_days = (datetime.datetime.now(datetime.timezone.utc) - created).days
        entry = {"image": img, "age_days": age_days, "status": "stale" if age_days > 30 else "recent"}
        results["images"].append(entry)
        if age_days > 90:
            results["alerts"].append({
                "severity": "medium",
                "image": img,
                "message": f"Image is {age_days} days old — consider updating"
            })
    except Exception as e:
        results["images"].append({"image": img, "error": str(e)})

with open("${REPORT_FILE}", "w") as f:
    json.dump(results, f, indent=2)
with open("${ALERT_FILE}", "w") as f:
    json.dump(results, f, indent=2)
print(f"Age proxy: {len(results['images'])} images, {len(results['alerts'])} stale")
PYEOF
    rm -rf "${TMP_DIR}"
    exit 0
fi

log_info "Using scanner: trivy"

# Run trivy scan across all images
echo "[]" > "${TMP_DIR}/all_alerts.json"
TOTAL_CRIT=0
TOTAL_HIGH=0

for image in "${IMAGES[@]}"; do
    [ -z "${image}" ] && continue
    log_info "Scanning: ${image}"

    SCAN_JSON=$(trivy image --severity CRITICAL,HIGH --format json --quiet "${image}" 2>/dev/null) || true

    if [ -n "${SCAN_JSON}" ]; then
        # Save per-image scan
        SAFE_NAME=$(echo "${image}" | tr '/:' '_')
        echo "${SCAN_JSON}" > "${REPORTS_DIR}/trivy_${SAFE_NAME}_$(date +%Y%m%d).json" 2>/dev/null || true

        # Extract alerts
        python3 -c "
import json, sys
scan = json.loads(sys.stdin.read())
image_name = sys.argv[1]
alerts_file = sys.argv[2]

with open(alerts_file) as f:
    all_alerts = json.load(f)

for r in scan.get('Results', []):
    for v in r.get('Vulnerabilities', []):
        sev = v.get('Severity', '')
        if sev in ('CRITICAL', 'HIGH'):
            all_alerts.append({
                'image': image_name,
                'severity': sev,
                'cve': v.get('VulnerabilityID', 'unknown'),
                'package': v.get('PkgName', 'unknown'),
                'installed': v.get('InstalledVersion', 'unknown'),
                'fixed': v.get('FixedVersion', 'unfixed'),
                'title': (v.get('Title') or '')[:120]
            })

# Deduplicate
seen = set()
deduped = []
for a in all_alerts:
    key = (a.get('cve'), a.get('package'))
    if key not in seen:
        seen.add(key)
        deduped.append(a)

with open(alerts_file, 'w') as f:
    json.dump(deduped, f)
" "${image}" "${TMP_DIR}/all_alerts.json" <<< "${SCAN_JSON}" 2>/dev/null || true
    fi
done

# Count from alerts file
TOTAL_VULNS=$(python3 -c "
import json
with open('${TMP_DIR}/all_alerts.json') as f:
    alerts = json.load(f)
crit = sum(1 for a in alerts if a['severity'] == 'CRITICAL')
high = sum(1 for a in alerts if a['severity'] == 'HIGH')
print(f'{crit} {high} {len(alerts)}')
" 2>/dev/null || echo "0 0 0")

read -r TOTAL_CRIT TOTAL_HIGH TOTAL_VULNS <<< "${TOTAL_VULNS}"

# Write final report
python3 << PYEOF
import json, datetime

with open("${TMP_DIR}/all_alerts.json") as f:
    alerts = json.load(f)

report = {
    "scan_date": datetime.datetime.now().isoformat(),
    "scanner": "trivy",
    "images_scanned": ${IMAGE_COUNT},
    "critical": ${TOTAL_CRIT},
    "high": ${TOTAL_HIGH},
    "total": ${TOTAL_VULNS},
    "alerts": alerts
}

with open("${REPORT_FILE}", "w") as f:
    json.dump(report, f, indent=2)
with open("${ALERT_FILE}", "w") as f:
    json.dump(report, f, indent=2)

print(f"Trivy: {report['images_scanned']} images, {report['critical']} critical, {report['high']} high")
PYEOF

rm -rf "${TMP_DIR}"
log_ok "CVE scan complete: ${IMAGE_COUNT} images, ${TOTAL_CRIT} critical, ${TOTAL_HIGH} high"

if [ "${TOTAL_CRIT}" -gt 0 ]; then
    log_warn "⚠️  ${TOTAL_CRIT} CRITICAL vulnerabilities found!"
fi
