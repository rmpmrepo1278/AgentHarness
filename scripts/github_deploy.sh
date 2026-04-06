#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# github_deploy.sh — Auto-install and configure a GitHub repo
#
# Usage:
#   ./github_deploy.sh https://github.com/user/repo
#   OR: Add to queue via Telegram/Chaguli, processed by scheduler.sh
#
# Workflow:
#   1. Clone the repo
#   2. Analyze: detect setup method (Docker, pip, npm, make, etc.)
#   3. Read README/docs for install instructions
#   4. Ask local LLM to generate an install plan
#   5. Execute the plan (with safety checks)
#   6. Verify the service is running
#   7. Report success/failure
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

DEPLOY_DIR="/opt/deployments"
GITHUB_QUEUE="/opt/agentharness/github_queue.json"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"

# Load env
[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

# =============================================================================
# Queue management
# =============================================================================
init_github_queue() {
    if [ ! -f "${GITHUB_QUEUE}" ]; then
        echo '[]' > "${GITHUB_QUEUE}"
    fi
}

add_repo_to_queue() {
    local repo_url="$1"
    local notes="${2:-}"

    init_github_queue

    python3 << PYEOF
import json
from datetime import datetime

queue = json.load(open("${GITHUB_QUEUE}"))

# Normalize URL
url = "${repo_url}".strip().rstrip('/')
if not url.startswith('http'):
    url = f"https://github.com/{url}"

# Extract repo name
name = url.rstrip('/').split('/')[-1].replace('.git', '')

# Don't add duplicates
if not any(r['url'] == url and r['status'] == 'pending' for r in queue):
    queue.append({
        'url': url,
        'name': name,
        'notes': '${notes}',
        'status': 'pending',
        'queued_at': datetime.now().isoformat()
    })
    json.dump(queue, open("${GITHUB_QUEUE}", 'w'), indent=2)
    print(f"Queued: {name} ({url})")
else:
    print(f"Already queued: {name}")
PYEOF
}

# =============================================================================
# Analyze a repo to determine install method
# =============================================================================
analyze_repo() {
    local repo_dir="$1"

    log_info "Analyzing repository structure..."

    local analysis=""

    # Check for Docker
    if [ -f "${repo_dir}/docker-compose.yml" ] || [ -f "${repo_dir}/docker-compose.yaml" ] || \
       [ -f "${repo_dir}/compose.yml" ] || [ -f "${repo_dir}/compose.yaml" ]; then
        analysis+="DOCKER_COMPOSE=true "
        local compose_file
        compose_file=$(ls "${repo_dir}"/docker-compose.y*ml "${repo_dir}"/compose.y*ml 2>/dev/null | head -1)
        analysis+="COMPOSE_FILE=${compose_file} "
    fi

    if [ -f "${repo_dir}/Dockerfile" ]; then
        analysis+="DOCKERFILE=true "
    fi

    # Check for Python
    if [ -f "${repo_dir}/requirements.txt" ]; then
        analysis+="PYTHON_REQUIREMENTS=true "
    fi
    if [ -f "${repo_dir}/setup.py" ] || [ -f "${repo_dir}/pyproject.toml" ]; then
        analysis+="PYTHON_PACKAGE=true "
    fi

    # Check for Node.js
    if [ -f "${repo_dir}/package.json" ]; then
        analysis+="NODEJS=true "
    fi

    # Check for Makefile
    if [ -f "${repo_dir}/Makefile" ]; then
        analysis+="MAKEFILE=true "
    fi

    # Check for shell install scripts
    if [ -f "${repo_dir}/install.sh" ] || [ -f "${repo_dir}/setup.sh" ]; then
        analysis+="INSTALL_SCRIPT=true "
    fi

    # Check for Go
    if [ -f "${repo_dir}/go.mod" ]; then
        analysis+="GOLANG=true "
    fi

    # Check for Rust
    if [ -f "${repo_dir}/Cargo.toml" ]; then
        analysis+="RUST=true "
    fi

    # Read README for context
    local readme=""
    for f in README.md readme.md README.rst README README.txt; do
        if [ -f "${repo_dir}/${f}" ]; then
            # First 200 lines of README
            readme=$(head -200 "${repo_dir}/${f}")
            analysis+="README=${f} "
            break
        fi
    done

    # Read .env.example if exists
    local env_example=""
    if [ -f "${repo_dir}/.env.example" ]; then
        env_example=$(cat "${repo_dir}/.env.example")
        analysis+="ENV_EXAMPLE=true "
    fi

    echo "${analysis}"
}

# =============================================================================
# Generate install plan using local LLM
# =============================================================================
generate_install_plan() {
    local repo_dir="$1"
    local repo_name="$2"
    local analysis="$3"

    log_info "Generating install plan via local LLM..."

    # Collect context
    local readme=""
    for f in README.md readme.md README.rst README; do
        [ -f "${repo_dir}/${f}" ] && { readme=$(head -200 "${repo_dir}/${f}"); break; }
    done

    local compose_content=""
    local compose_file
    compose_file=$(ls "${repo_dir}"/docker-compose.y*ml "${repo_dir}"/compose.y*ml 2>/dev/null | head -1)
    [ -n "${compose_file}" ] && compose_content=$(cat "${compose_file}")

    local env_example=""
    [ -f "${repo_dir}/.env.example" ] && env_example=$(cat "${repo_dir}/.env.example")

    local plan
    plan=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json

readme = open('${repo_dir}/README.md').read()[:3000] if __import__('os').path.exists('${repo_dir}/README.md') else 'No README'

print(json.dumps({
    'messages': [
        {'role': 'system', 'content': '''You are a homelab sysadmin installing software on Debian with Docker.
Generate a step-by-step install plan as a JSON array of commands.
Each step: {\"description\": \"...\", \"command\": \"...\", \"can_fail\": true/false}
Rules:
- Prefer docker-compose if available
- Use the homelab Docker network
- Don't expose unnecessary ports to 0.0.0.0 (use 127.0.0.1 where possible)
- Create .env from .env.example if it exists
- Set restart: unless-stopped for Docker services
- Output ONLY valid JSON array, no other text'''},
        {'role': 'user', 'content': f\"\"\"Install this repo: ${repo_name}
Analysis: ${analysis}
README (first 3000 chars):
{readme}
Docker Compose:
${compose_content}
.env.example:
${env_example}\"\"\"}
    ],
    'max_tokens': 1000,
    'temperature': 0.1
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    content = d['choices'][0]['message']['content'].strip()
    if content.startswith('\`\`\`'):
        content = content.split('\n', 1)[1].rsplit('\`\`\`', 1)[0].strip()
    # Validate JSON
    steps = json.loads(content)
    print(json.dumps(steps, indent=2))
except Exception as e:
    print(f'[]')
" 2>/dev/null || echo "[]")

    echo "${plan}"
}

# =============================================================================
# Execute install plan with safety checks
# =============================================================================
execute_plan() {
    local repo_name="$1"
    local plan="$2"
    local repo_dir="$3"

    log_info "Executing install plan for ${repo_name}..."

    local step_count
    step_count=$(echo "${plan}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [ "${step_count}" -eq 0 ]; then
        log_error "No install steps generated. Falling back to defaults."
        fallback_install "${repo_dir}" "${repo_name}"
        return $?
    fi

    local success=true
    local step_num=0

    echo "${plan}" | python3 -c "
import sys, json
steps = json.load(sys.stdin)
for s in steps:
    print(f\"{s.get('description', 'unknown')}|||{s.get('command', '')}|||{s.get('can_fail', False)}\")
" 2>/dev/null | while IFS='|||' read -r description command can_fail; do
        ((step_num++))
        log_info "Step ${step_num}/${step_count}: ${description}"

        # Safety checks — block dangerous commands
        if echo "${command}" | grep -qiE "rm -rf /[^o]|dd if=|mkfs\.|:(){ :|curl.*\| ?sh|wget.*\| ?sh"; then
            log_error "BLOCKED: Dangerous command detected: ${command}"
            echo "- BLOCKED dangerous command: ${command}" >> "${CLEANUP_REPORT:-/dev/null}"
            continue
        fi

        # Execute from repo directory
        if (cd "${repo_dir}" && eval "${command}") 2>&1; then
            log_ok "Step ${step_num}: OK"
        else
            if [ "${can_fail}" = "True" ] || [ "${can_fail}" = "true" ]; then
                log_warn "Step ${step_num}: Failed (non-critical, continuing)"
            else
                log_error "Step ${step_num}: Failed"
                success=false
                break
            fi
        fi
    done

    if [ "${success}" = true ]; then
        return 0
    else
        return 1
    fi
}

# =============================================================================
# Fallback install (no LLM or plan failed)
# =============================================================================
fallback_install() {
    local repo_dir="$1"
    local repo_name="$2"

    log_info "Attempting fallback install..."

    cd "${repo_dir}"

    # Try docker-compose first
    local compose_file
    compose_file=$(ls docker-compose.y*ml compose.y*ml 2>/dev/null | head -1)
    if [ -n "${compose_file}" ]; then
        log_info "Found ${compose_file}, running docker compose up..."
        [ -f .env.example ] && [ ! -f .env ] && cp .env.example .env
        docker compose up -d 2>&1 && return 0
    fi

    # Try install.sh
    if [ -f install.sh ]; then
        log_info "Found install.sh, running..."
        chmod +x install.sh
        bash install.sh 2>&1 && return 0
    fi

    # Try pip install
    if [ -f requirements.txt ]; then
        log_info "Found requirements.txt, installing..."
        pip install -r requirements.txt 2>&1 && return 0
    fi

    # Try npm install
    if [ -f package.json ]; then
        log_info "Found package.json, installing..."
        npm install 2>&1 && return 0
    fi

    # Try make
    if [ -f Makefile ]; then
        log_info "Found Makefile, building..."
        make 2>&1 && return 0
    fi

    log_error "No recognized install method found"
    return 1
}

# =============================================================================
# Deploy a single repo
# =============================================================================
deploy_repo() {
    local repo_url="$1"
    local repo_name="$2"

    log_header "Deploying: ${repo_name}"

    ensure_dir "${DEPLOY_DIR}"
    local repo_dir="${DEPLOY_DIR}/${repo_name}"

    # Clone or update
    if [ -d "${repo_dir}" ]; then
        log_info "Repository exists, pulling latest..."
        cd "${repo_dir}" && git pull --ff-only 2>/dev/null || {
            log_warn "Pull failed. Re-cloning..."
            rm -rf "${repo_dir}"
            git clone "${repo_url}" "${repo_dir}"
        }
    else
        log_info "Cloning ${repo_url}..."
        git clone "${repo_url}" "${repo_dir}"
    fi

    # Analyze
    local analysis
    analysis=$(analyze_repo "${repo_dir}")
    log_info "Detected: ${analysis}"

    # Generate plan
    local plan
    plan=$(generate_install_plan "${repo_dir}" "${repo_name}" "${analysis}")

    # Execute
    if execute_plan "${repo_name}" "${plan}" "${repo_dir}"; then
        log_ok "${repo_name} deployed successfully"

        # Refresh service registry and sync OpenClaw skills
        log_info "Refreshing service registry after deployment..."
        bash "${SCRIPT_DIR}/service_registry.sh" 2>/dev/null || true
        bash "${SCRIPT_DIR}/openclaw_sync.sh" 2>/dev/null || {
            touch /opt/agentharness/service_registry_dirty
        }

        return 0
    else
        log_error "${repo_name} deployment failed"
        return 1
    fi
}

# =============================================================================
# Process the GitHub queue
# =============================================================================
process_queue() {
    init_github_queue

    python3 << 'PYEOF'
import json, subprocess, os
from datetime import datetime

queue_path = os.environ.get('GITHUB_QUEUE', '/opt/agentharness/github_queue.json')
queue = json.load(open(queue_path))

pending = [r for r in queue if r['status'] == 'pending']
if not pending:
    print("No pending repos to deploy")
    exit(0)

script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '/opt/agentharness'

for repo in pending:
    repo['status'] = 'deploying'
    repo['started_at'] = datetime.now().isoformat()
    json.dump(queue, open(queue_path, 'w'), indent=2)

    print(f"Deploying: {repo['name']} ({repo['url']})")

    result = subprocess.run(
        ['bash', '-c', f"source /opt/agentharness/scripts/common.sh && source /opt/agentharness/scripts/github_deploy.sh && deploy_repo '{repo['url']}' '{repo['name']}'"],
        capture_output=True, text=True, timeout=1800
    )

    if result.returncode == 0:
        repo['status'] = 'deployed'
        repo['deployed_at'] = datetime.now().isoformat()
        print(f"  Success: {repo['name']}")
    else:
        repo['status'] = 'failed'
        repo['error'] = result.stderr[:500]
        repo['failed_at'] = datetime.now().isoformat()
        print(f"  Failed: {repo['name']} - {result.stderr[:200]}")

    json.dump(queue, open(queue_path, 'w'), indent=2)
PYEOF
}

# =============================================================================
# Main
# =============================================================================
main() {
    # If called with a URL argument, add to queue and deploy immediately
    if [ $# -ge 1 ]; then
        local url="$1"
        local notes="${2:-}"

        # Extract name
        local name
        name=$(echo "${url}" | sed 's|.*/||; s|\.git$||')

        # Deploy directly
        deploy_repo "${url}" "${name}"
    else
        # Process queue (called by scheduler)
        GITHUB_QUEUE="${GITHUB_QUEUE}" process_queue
    fi
}

main "$@"
