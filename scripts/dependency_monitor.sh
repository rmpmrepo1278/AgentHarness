#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# dependency_monitor.sh — Check for Docker image and project updates
#
# Checks:
# 1. Docker images with running containers — age analysis
# 2. GitHub repos with local clones — upstream commits?
# 3. System packages — security updates?
#
# Schedule: Weekly via harness_registry.yaml
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REPORTS_DIR="${AH_REPORTS_DIR:-/home/rohit/agentharness/data/reports}"
DATA_DIR="${AH_DATA_DIR:-/home/rohit/agentharness/data}"
REPORT_FILE="${REPORTS_DIR}/dependency_check_$(date +%Y%m%d).json"
LATEST_FILE="${DATA_DIR}/dependency_latest.json"
TMP_DIR=$(mktemp -d)

mkdir -p "${REPORTS_DIR}" "${DATA_DIR}"

log_info "Starting dependency update check..."

# --- 1. Docker Image Age Analysis ---
python3 << PYEOF
import json, subprocess, datetime

images_output = subprocess.check_output(["docker", "ps", "--format", "{{.Image}}"]).decode().strip()
images = sorted(set(filter(None, images_output.split("\n"))))
results = []

for img in images:
    try:
        inspect = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.Created}}", img],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        created = datetime.datetime.fromisoformat(inspect.replace("Z", "+00:00"))
        age_days = (datetime.datetime.now(datetime.timezone.utc) - created).days
        status = "current" if age_days < 30 else ("stale" if age_days < 90 else "very_stale")
        results.append({
            "type": "docker_image",
            "image": img,
            "age_days": age_days,
            "status": status,
            "recommendation": "update" if age_days > 30 else "ok"
        })
    except Exception as e:
        results.append({"type": "docker_image", "image": img, "error": str(e)})

with open("${TMP_DIR}/docker.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"Docker: {len(results)} images analyzed")
PYEOF

# --- 2. GitHub Repo Updates ---
python3 << PYEOF
import json, subprocess, datetime, os

repo_dirs = [
    "/home/rohit/.hermes/hermes-agent",
    "/home/rohit/ik_Ollama",
    "/home/rohit/projects/career-ops",
    "/home/rohit/projects/agent-traces",
]
results = []

for repo_dir in repo_dirs:
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        continue
    repo_name = os.path.basename(repo_dir)
    try:
        subprocess.check_call(["git", "fetch", "--quiet"], cwd=repo_dir,
                              stderr=subprocess.DEVNULL)
        local = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=repo_dir).decode().strip()
        try:
            remote = subprocess.check_output(["git", "rev-parse", "@{u}"],
                                              cwd=repo_dir).decode().strip()
            behind = int(subprocess.check_output(
                ["git", "rev-list", "--count", "HEAD..@{u}"],
                cwd=repo_dir).decode().strip())
        except subprocess.CalledProcessError:
            remote = "no_upstream"
            behind = 0

        if behind > 0:
            results.append({
                "type": "git_repo",
                "repo": repo_name,
                "path": repo_dir,
                "commits_behind": behind,
                "local_commit": local[:12],
                "remote_commit": remote[:12],
                "status": "behind"
            })
        else:
            results.append({
                "type": "git_repo",
                "repo": repo_name,
                "status": "up_to_date"
            })
    except Exception as e:
        results.append({"type": "git_repo", "repo": repo_name, "error": str(e)})

import os
with open("${TMP_DIR}/repos.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"Repos: {len(results)} checked")
PYEOF

# --- 3. System Security Updates ---
python3 << PYEOF
import json, subprocess

results = []
try:
    upgradable = subprocess.check_output(
        ["apt", "list", "--upgradable"],
        stderr=subprocess.DEVNULL
    ).decode().strip().split("\n")
    total = max(0, len(upgradable) - 1)  # skip header
    security = sum(1 for line in upgradable if "-security" in line)
    results.append({
        "type": "system_packages",
        "total_upgradable": total,
        "security_updates": security,
        "status": "updates_available" if total > 0 else "up_to_date"
    })
except Exception as e:
    results.append({"type": "system_packages", "error": str(e)})

with open("${TMP_DIR}/system.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"System: {results[0].get('total_upgradable', '?')} updates")
PYEOF

# --- Combine Report ---
python3 << PYEOF
import json, datetime

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []

report = {
    "check_date": datetime.datetime.now().isoformat(),
    "docker_images": load_json("${TMP_DIR}/docker.json"),
    "git_repos": load_json("${TMP_DIR}/repos.json"),
    "system_packages": load_json("${TMP_DIR}/system.json"),
    "summary": {
        "docker_stale": sum(1 for d in load_json("${TMP_DIR}/docker.json")
                           if d.get("status") in ("stale", "very_stale")),
        "repo_behind": sum(1 for r in load_json("${TMP_DIR}/repos.json")
                          if r.get("status") == "behind"),
        "system_updates": sum(c.get("total_upgradable", 0)
                             for c in load_json("${TMP_DIR}/system.json"))
    }
}

with open("${REPORT_FILE}", "w") as f:
    json.dump(report, f, indent=2)
with open("${LATEST_FILE}", "w") as f:
    json.dump(report, f, indent=2)

s = report["summary"]
print(f"Report: {s['docker_stale']} stale images, {s['repo_behind']} repos behind, {s['system_updates']} system updates")
PYEOF

rm -rf "${TMP_DIR}"
log_ok "Dependency check complete"
