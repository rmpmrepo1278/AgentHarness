#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# discover_automations.sh — Deep scan of ALL existing automations, scripts,
#                           services, cron jobs, workflows, and configs
#
# Produces a comprehensive catalog so AgentHarness can:
#   1. Understand what already exists
#   2. Augment rather than replace
#   3. Identify gaps (what's missing)
#   4. Wire into existing automations
#
# Output: /opt/agentharness/automation_catalog.json
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

CATALOG="/opt/agentharness/automation_catalog.json"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"

# Load env if exists
[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

# =============================================================================
# Collectors — each scans one type of automation
# =============================================================================

# -----------------------------------------------------------------------------
# 1. Find all shell scripts
# -----------------------------------------------------------------------------
collect_shell_scripts() {
    log_info "Scanning for shell scripts..."

    find /opt /home /root /usr/local/bin /usr/local/sbin \
        -maxdepth 5 \
        \( -name "*.sh" -o -name "*.bash" \) \
        -type f \
        2>/dev/null | while read -r script; do

        # Skip AgentHarness's own scripts
        [[ "${script}" == *agentharness-install* ]] && continue
        [[ "${script}" == *AgentHarness/scripts* ]] && continue

        local size
        size=$(stat -c%s "${script}" 2>/dev/null || echo "0")
        local modified
        modified=$(stat -c%Y "${script}" 2>/dev/null || echo "0")
        local executable="false"
        [ -x "${script}" ] && executable="true"

        # Extract shebang and first comment block for purpose
        local shebang=""
        local description=""
        shebang=$(head -1 "${script}" 2>/dev/null || echo "")
        # Look for description in first 10 lines (comments)
        description=$(head -20 "${script}" 2>/dev/null | grep -E '^#[^!]' | head -5 | sed 's/^#\s*//' | tr '\n' ' ' | head -c 200)

        # Detect what it does by scanning content
        local capabilities=""
        local content
        content=$(cat "${script}" 2>/dev/null || echo "")

        echo "${content}" | grep -qi "docker\|container" && capabilities+="docker,"
        echo "${content}" | grep -qi "systemctl\|service" && capabilities+="systemd,"
        echo "${content}" | grep -qi "curl.*api\|wget\|http" && capabilities+="http,"
        echo "${content}" | grep -qi "telegram\|bot.*token" && capabilities+="telegram,"
        echo "${content}" | grep -qi "backup\|rsync\|cp.*-r" && capabilities+="backup,"
        echo "${content}" | grep -qi "monitor\|health\|check\|ping" && capabilities+="monitoring,"
        echo "${content}" | grep -qi "restart\|heal\|recover\|fix" && capabilities+="self-healing,"
        echo "${content}" | grep -qi "cleanup\|prune\|remove\|delete" && capabilities+="cleanup,"
        echo "${content}" | grep -qi "benchmark\|bench\|test\|perf" && capabilities+="benchmark,"
        echo "${content}" | grep -qi "llama\|llm\|model\|inference" && capabilities+="llm,"
        echo "${content}" | grep -qi "cron\|schedule\|timer" && capabilities+="scheduling,"
        echo "${content}" | grep -qi "log\|journal\|syslog" && capabilities+="logging,"
        echo "${content}" | grep -qi "network\|dns\|ip\|route\|firewall" && capabilities+="network,"
        echo "${content}" | grep -qi "git\|clone\|pull\|push" && capabilities+="git,"
        echo "${content}" | grep -qi "install\|setup\|deploy\|configure" && capabilities+="deployment,"
        capabilities=$(echo "${capabilities}" | sed 's/,$//')

        # Output as JSON line
        python3 -c "
import json
print(json.dumps({
    'type': 'shell_script',
    'path': '${script}',
    'size': ${size},
    'modified': ${modified},
    'executable': ${executable},
    'description': '''${description}'''[:200],
    'capabilities': '${capabilities}'.split(',') if '${capabilities}' else [],
    'shebang': '''${shebang}'''
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 2. Find all Python scripts
# -----------------------------------------------------------------------------
collect_python_scripts() {
    log_info "Scanning for Python scripts..."

    find /opt /home /root /usr/local/bin \
        -maxdepth 5 \
        -name "*.py" \
        -type f \
        -not -path "*/site-packages/*" \
        -not -path "*/.venv/*" \
        -not -path "*/venv/*" \
        -not -path "*/__pycache__/*" \
        -not -path "*/node_modules/*" \
        2>/dev/null | while read -r script; do

        [[ "${script}" == *agentharness-install* ]] && continue
        [[ "${script}" == *AgentHarness* ]] && continue

        local size
        size=$(stat -c%s "${script}" 2>/dev/null || echo "0")
        local modified
        modified=$(stat -c%Y "${script}" 2>/dev/null || echo "0")

        # Extract docstring or first comments
        local description=""
        description=$(python3 -c "
import ast, sys
try:
    with open('${script}') as f:
        tree = ast.parse(f.read())
    ds = ast.get_docstring(tree)
    if ds:
        print(ds[:200])
    else:
        # Fall back to first comment
        with open('${script}') as f:
            for line in f:
                if line.startswith('#') and not line.startswith('#!'):
                    print(line.strip('# \n')[:200])
                    break
except:
    pass
" 2>/dev/null)

        # Detect capabilities from imports and content
        local capabilities=""
        local content
        content=$(cat "${script}" 2>/dev/null || echo "")

        echo "${content}" | grep -qi "import docker\|from docker" && capabilities+="docker,"
        echo "${content}" | grep -qi "import requests\|import httpx\|import aiohttp\|urllib" && capabilities+="http,"
        echo "${content}" | grep -qi "telegram\|telebot\|aiogram" && capabilities+="telegram,"
        echo "${content}" | grep -qi "flask\|fastapi\|uvicorn\|starlette" && capabilities+="web_server,"
        echo "${content}" | grep -qi "subprocess\|os\.system\|shutil" && capabilities+="system,"
        echo "${content}" | grep -qi "monitor\|health\|check\|watchdog" && capabilities+="monitoring,"
        echo "${content}" | grep -qi "schedule\|cron\|apscheduler\|celery" && capabilities+="scheduling,"
        echo "${content}" | grep -qi "openai\|anthropic\|groq\|llama" && capabilities+="llm,"
        echo "${content}" | grep -qi "smtp\|email\|sendmail" && capabilities+="email,"
        echo "${content}" | grep -qi "sqlite\|postgres\|mysql\|redis" && capabilities+="database,"
        echo "${content}" | grep -qi "selenium\|playwright\|beautifulsoup\|scrapy" && capabilities+="web_scraping,"
        echo "${content}" | grep -qi "json\|yaml\|toml\|configparser" && capabilities+="config,"
        echo "${content}" | grep -qi "logging\|logger" && capabilities+="logging,"
        echo "${content}" | grep -qi "argparse\|click\|typer\|fire" && capabilities+="cli,"
        capabilities=$(echo "${capabilities}" | sed 's/,$//')

        # Detect imports
        local imports=""
        imports=$(grep -E "^import |^from " "${script}" 2>/dev/null | head -20 | tr '\n' '|' | head -c 300)

        python3 -c "
import json
print(json.dumps({
    'type': 'python_script',
    'path': '${script}',
    'size': ${size},
    'modified': ${modified},
    'description': '''${description}'''[:200],
    'capabilities': '${capabilities}'.split(',') if '${capabilities}' else [],
    'key_imports': '''${imports}'''[:300]
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 3. Collect cron jobs
# -----------------------------------------------------------------------------
collect_cron_jobs() {
    log_info "Scanning cron jobs..."

    # User crontab
    crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | while read -r line; do
        local schedule command
        # Extract schedule (first 5 fields) and command
        schedule=$(echo "${line}" | awk '{print $1,$2,$3,$4,$5}')
        command=$(echo "${line}" | awk '{for(i=6;i<=NF;i++) printf "%s ", $i; print ""}' | xargs)

        [ -z "${command}" ] && continue

        python3 -c "
import json
print(json.dumps({
    'type': 'cron_job',
    'user': '$(whoami)',
    'schedule': '${schedule}',
    'command': '''${command}'''[:500],
    'source': 'user_crontab'
}))
" 2>/dev/null
    done

    # Root crontab
    sudo crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | while read -r line; do
        local schedule command
        schedule=$(echo "${line}" | awk '{print $1,$2,$3,$4,$5}')
        command=$(echo "${line}" | awk '{for(i=6;i<=NF;i++) printf "%s ", $i; print ""}' | xargs)

        [ -z "${command}" ] && continue

        python3 -c "
import json
print(json.dumps({
    'type': 'cron_job',
    'user': 'root',
    'schedule': '${schedule}',
    'command': '''${command}'''[:500],
    'source': 'root_crontab'
}))
" 2>/dev/null
    done

    # System cron directories
    for crondir in /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly; do
        [ -d "${crondir}" ] || continue
        for f in "${crondir}"/*; do
            [ -f "${f}" ] || continue
            python3 -c "
import json
print(json.dumps({
    'type': 'cron_job',
    'user': 'system',
    'schedule': '$(basename $(dirname ${f}))',
    'command': '${f}',
    'source': '${crondir}'
}))
" 2>/dev/null
        done
    done
}

# -----------------------------------------------------------------------------
# 4. Collect custom systemd services
# -----------------------------------------------------------------------------
collect_systemd_services() {
    log_info "Scanning custom systemd services..."

    # Find non-default services
    find /etc/systemd/system -maxdepth 1 -name "*.service" -type f 2>/dev/null | while read -r svc; do
        local name
        name=$(basename "${svc}" .service)

        # Skip default system services
        [[ "${name}" == systemd-* ]] && continue
        [[ "${name}" == dbus* ]] && continue

        local status
        status=$(systemctl is-active "${name}" 2>/dev/null || echo "unknown")
        local enabled
        enabled=$(systemctl is-enabled "${name}" 2>/dev/null || echo "unknown")

        # Extract ExecStart
        local exec_start
        exec_start=$(grep -oP '(?<=ExecStart=).*' "${svc}" 2>/dev/null | head -1 || echo "")
        local description
        description=$(grep -oP '(?<=Description=).*' "${svc}" 2>/dev/null | head -1 || echo "")

        python3 -c "
import json
print(json.dumps({
    'type': 'systemd_service',
    'name': '${name}',
    'path': '${svc}',
    'status': '${status}',
    'enabled': '${enabled}',
    'exec_start': '''${exec_start}'''[:300],
    'description': '''${description}'''[:200]
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 5. Collect Docker compose files and their services
# -----------------------------------------------------------------------------
collect_docker_composes() {
    log_info "Scanning Docker Compose files..."

    find /opt /home /root \
        -maxdepth 4 \
        \( -name "docker-compose.yml" -o -name "docker-compose.yaml" \
           -o -name "compose.yml" -o -name "compose.yaml" \) \
        -type f \
        2>/dev/null | while read -r compose; do

        local dir
        dir=$(dirname "${compose}")

        # Extract service names
        local services
        services=$(python3 -c "
import yaml, json
try:
    with open('${compose}') as f:
        data = yaml.safe_load(f)
    svcs = list(data.get('services', {}).keys())
    print(json.dumps(svcs))
except:
    print('[]')
" 2>/dev/null || echo "[]")

        # Check for .env in same dir
        local has_env="false"
        [ -f "${dir}/.env" ] && has_env="true"

        python3 -c "
import json
print(json.dumps({
    'type': 'docker_compose',
    'path': '${compose}',
    'directory': '${dir}',
    'services': ${services},
    'has_env': ${has_env}
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 6. Collect Docker container healthchecks
# -----------------------------------------------------------------------------
collect_docker_healthchecks() {
    log_info "Scanning Docker container health configurations..."

    docker ps --format '{{.Names}}' 2>/dev/null | while read -r container; do
        local healthcheck
        healthcheck=$(docker inspect --format='{{json .Config.Healthcheck}}' "${container}" 2>/dev/null || echo "null")

        local restart_policy
        restart_policy=$(docker inspect --format='{{.HostConfig.RestartPolicy.Name}}' "${container}" 2>/dev/null || echo "unknown")

        [ "${healthcheck}" = "null" ] && [ "${restart_policy}" = "no" ] && continue

        python3 -c "
import json
print(json.dumps({
    'type': 'docker_healthcheck',
    'container': '${container}',
    'healthcheck': ${healthcheck} if '${healthcheck}' != 'null' else None,
    'restart_policy': '${restart_policy}'
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 7. Collect n8n workflows (if n8n is running)
# -----------------------------------------------------------------------------
collect_n8n_workflows() {
    local n8n_url="${N8N_URL:-}"
    local n8n_key="${N8N_API_KEY:-}"

    [ -z "${n8n_url}" ] && return 0

    log_info "Scanning n8n workflows..."

    local workflows
    workflows=$(curl -sf "${n8n_url}/api/v1/workflows" \
        -H "X-N8N-API-KEY: ${n8n_key}" 2>/dev/null || echo "")

    [ -z "${workflows}" ] && return 0

    python3 -c "
import json
try:
    data = json.loads('''${workflows}''')
    for wf in data.get('data', []):
        print(json.dumps({
            'type': 'n8n_workflow',
            'id': wf.get('id', ''),
            'name': wf.get('name', ''),
            'active': wf.get('active', False),
            'nodes': len(wf.get('nodes', [])),
            'created': wf.get('createdAt', ''),
            'updated': wf.get('updatedAt', '')
        }))
except:
    pass
" 2>/dev/null
}

# -----------------------------------------------------------------------------
# 8. Scan for config/YAML/TOML files that drive automations
# -----------------------------------------------------------------------------
collect_config_files() {
    log_info "Scanning for automation config files..."

    find /opt /home /root \
        -maxdepth 4 \
        \( -name "*.yaml" -o -name "*.yml" -o -name "*.toml" -o -name "*.ini" -o -name "*.conf" \) \
        -type f \
        -not -path "*/node_modules/*" \
        -not -path "*/.venv/*" \
        -not -path "*/site-packages/*" \
        -newer /etc/hostname \
        2>/dev/null | while read -r config; do

        # Only include files that look like automation configs
        local content
        content=$(head -50 "${config}" 2>/dev/null || echo "")
        local is_automation=false

        echo "${content}" | grep -qi "schedule\|cron\|trigger\|webhook\|monitor\|alert\|automat" && is_automation=true
        echo "${content}" | grep -qi "docker\|container\|service\|health" && is_automation=true
        echo "${content}" | grep -qi "llm\|model\|inference\|api_key" && is_automation=true

        [ "${is_automation}" = false ] && continue

        local size
        size=$(stat -c%s "${config}" 2>/dev/null || echo "0")

        python3 -c "
import json
print(json.dumps({
    'type': 'config_file',
    'path': '${config}',
    'size': ${size},
    'preview': '''${content}'''[:300]
}))
" 2>/dev/null
    done
}

# -----------------------------------------------------------------------------
# 9. Discover OpenClaw installation, workspace, skills, and AGENTS.md
# -----------------------------------------------------------------------------
collect_openclaw() {
    log_info "Scanning for OpenClaw installation..."

    local openclaw_paths="/opt/agentharness/openclaw_paths.env"
    > "${openclaw_paths}"

    # Find openclaw binary
    local openclaw_bin=""
    if command -v openclaw &>/dev/null; then
        openclaw_bin=$(which openclaw)
    else
        # Search common locations
        for p in /usr/local/bin/openclaw /usr/bin/openclaw /opt/openclaw/bin/openclaw \
                 "$HOME/.local/bin/openclaw" "$HOME/.openclaw/bin/openclaw"; do
            [ -x "$p" ] && openclaw_bin="$p" && break
        done
    fi

    if [ -n "${openclaw_bin}" ]; then
        echo "OPENCLAW_BIN=${openclaw_bin}" >> "${openclaw_paths}"
        log_ok "OpenClaw binary: ${openclaw_bin}"
    fi

    # Find openclaw home directory
    local openclaw_home=""
    for p in "$HOME/.openclaw" /opt/openclaw /etc/openclaw; do
        if [ -d "$p" ]; then
            openclaw_home="$p"
            break
        fi
    done

    # Also check if OPENCLAW_HOME env var is set
    [ -n "${OPENCLAW_HOME:-}" ] && [ -d "${OPENCLAW_HOME}" ] && openclaw_home="${OPENCLAW_HOME}"

    if [ -n "${openclaw_home}" ]; then
        echo "OPENCLAW_HOME=${openclaw_home}" >> "${openclaw_paths}"
        log_ok "OpenClaw home: ${openclaw_home}"
    fi

    # Find the config file
    local openclaw_config=""
    for p in "${openclaw_home}/openclaw.json" "${openclaw_home}/config.json" \
             "$HOME/.config/openclaw/openclaw.json"; do
        if [ -f "$p" ]; then
            openclaw_config="$p"
            echo "OPENCLAW_CONFIG=${openclaw_config}" >> "${openclaw_paths}"
            log_ok "OpenClaw config: ${openclaw_config}"
            break
        fi
    done

    # Find workspace directory
    local workspace=""
    # Try reading from config
    if [ -n "${openclaw_config}" ]; then
        workspace=$(python3 -c "
import json
try:
    cfg = json.load(open('${openclaw_config}'))
    # Check various config structures for workspace path
    ws = cfg.get('workspace', cfg.get('workspacePath', cfg.get('agent', {}).get('workspace', '')))
    if ws:
        print(ws)
except:
    pass
" 2>/dev/null)
    fi

    # Fallback: scan for workspace directory
    if [ -z "${workspace}" ] || [ ! -d "${workspace}" ]; then
        for p in "${openclaw_home}/workspace" "${openclaw_home}/workspaces/default" \
                 "$HOME/.openclaw/workspace" "$HOME/.openclaw/workspaces"; do
            if [ -d "$p" ]; then
                workspace="$p"
                break
            fi
        done
    fi

    if [ -n "${workspace}" ] && [ -d "${workspace}" ]; then
        echo "OPENCLAW_WORKSPACE=${workspace}" >> "${openclaw_paths}"
        log_ok "OpenClaw workspace: ${workspace}"

        python3 -c "
import json
print(json.dumps({
    'type': 'openclaw_workspace',
    'path': '${workspace}',
    'description': 'OpenClaw agent workspace directory'
}))
" 2>/dev/null
    fi

    # Find skills directory
    local skills_dir=""
    for p in "${workspace}/skills" "${openclaw_home}/skills" "${workspace}/../skills"; do
        if [ -d "$p" ]; then
            skills_dir=$(cd "$p" && pwd)
            break
        fi
    done

    if [ -n "${skills_dir}" ]; then
        echo "OPENCLAW_SKILLS_DIR=${skills_dir}" >> "${openclaw_paths}"
        log_ok "OpenClaw skills: ${skills_dir}"

        # List existing skills
        find "${skills_dir}" -name "SKILL.md" -type f 2>/dev/null | while read -r skill_file; do
            local skill_name
            skill_name=$(basename "$(dirname "${skill_file}")")
            local skill_desc
            skill_desc=$(head -10 "${skill_file}" 2>/dev/null | grep -oP '(?<=description: ).*' | head -1 || echo "")

            python3 -c "
import json
print(json.dumps({
    'type': 'openclaw_skill',
    'name': '${skill_name}',
    'path': '${skill_file}',
    'description': '''${skill_desc}'''[:200]
}))
" 2>/dev/null
        done
    fi

    # Find AGENTS.md
    local agents_md=""
    for p in "${workspace}/AGENTS.md" "${openclaw_home}/AGENTS.md" \
             "${workspace}/agents.md"; do
        if [ -f "$p" ]; then
            agents_md="$p"
            break
        fi
    done

    if [ -n "${agents_md}" ]; then
        echo "OPENCLAW_AGENTS_MD=${agents_md}" >> "${openclaw_paths}"
        log_ok "OpenClaw AGENTS.md: ${agents_md}"

        local line_count
        line_count=$(wc -l < "${agents_md}")
        python3 -c "
import json
print(json.dumps({
    'type': 'openclaw_agents_md',
    'path': '${agents_md}',
    'lines': ${line_count}
}))
" 2>/dev/null
    fi

    # Find SOUL.md and TOOLS.md
    for mdfile in SOUL.md TOOLS.md; do
        for p in "${workspace}/${mdfile}" "${openclaw_home}/${mdfile}"; do
            if [ -f "$p" ]; then
                local varname
                varname=$(echo "OPENCLAW_${mdfile}" | tr '.' '_' | tr '[:lower:]' '[:upper:]')
                echo "${varname}=$p" >> "${openclaw_paths}"
                log_ok "OpenClaw ${mdfile}: $p"
                break
            fi
        done
    done

    # Check if OpenClaw Gateway is running
    local gateway_running=false
    if pgrep -f "openclaw" &>/dev/null; then
        gateway_running=true
        log_ok "OpenClaw Gateway: running"
    fi
    local oc_container
    oc_container=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "openclaw" | head -1 || true)
    if [ -n "${oc_container}" ]; then
        gateway_running=true
        echo "OPENCLAW_CONTAINER=${oc_container}" >> "${openclaw_paths}"
        log_ok "OpenClaw container: ${oc_container}"
    fi

    echo "OPENCLAW_GATEWAY_RUNNING=${gateway_running}" >> "${openclaw_paths}"

    # Discover Telegram channel config
    if [ -n "${openclaw_config}" ]; then
        local has_telegram
        has_telegram=$(python3 -c "
import json
try:
    cfg = json.load(open('${openclaw_config}'))
    channels = cfg.get('channels', {})
    if 'telegram' in channels:
        print('yes')
except:
    pass
" 2>/dev/null)
        if [ "${has_telegram}" = "yes" ]; then
            echo "OPENCLAW_HAS_TELEGRAM=true" >> "${openclaw_paths}"
            log_ok "OpenClaw Telegram channel: configured"
        fi
    fi

    log_ok "OpenClaw paths saved to ${openclaw_paths}"
}

# =============================================================================
# Assemble the catalog
# =============================================================================
assemble_catalog() {
    log_header "Assembling Automation Catalog"

    ensure_dir /opt/agentharness

    # Collect all items into a single JSON array
    {
        collect_shell_scripts
        collect_python_scripts
        collect_cron_jobs
        collect_systemd_services
        collect_docker_composes
        collect_docker_healthchecks
        collect_n8n_workflows
        collect_config_files
        collect_openclaw
    } | python3 -c "
import sys, json

items = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        items.append(json.loads(line))
    except json.JSONDecodeError:
        pass

# Sort by type then path/name
items.sort(key=lambda x: (x.get('type', ''), x.get('path', x.get('name', ''))))

# Write catalog
with open('${CATALOG}', 'w') as f:
    json.dump({
        'discovered_at': '$(date -Iseconds)',
        'hostname': '$(hostname)',
        'total_items': len(items),
        'by_type': {},
        'items': items
    }, f, indent=2)

# Count by type
counts = {}
for item in items:
    t = item.get('type', 'unknown')
    counts[t] = counts.get(t, 0) + 1

# Update by_type in catalog
catalog = json.load(open('${CATALOG}'))
catalog['by_type'] = counts
json.dump(catalog, open('${CATALOG}', 'w'), indent=2)

# Print summary
print(f'Total automations discovered: {len(items)}')
for t, c in sorted(counts.items()):
    print(f'  {t}: {c}')
" 2>/dev/null
}

# =============================================================================
# LLM analysis — categorize automations and identify gaps
# =============================================================================
analyze_with_llm() {
    if ! curl -sf "${LLM_URL}/health" &>/dev/null; then
        log_warn "LLM not available. Skipping AI analysis."
        return 0
    fi

    log_info "Asking local LLM to analyze automation landscape..."

    # Build a summary (not the full catalog — too large)
    local summary
    summary=$(python3 -c "
import json

catalog = json.load(open('${CATALOG}'))
items = catalog['items']

# Group by type with key details
summary = []
for item in items:
    t = item.get('type')
    if t == 'shell_script':
        summary.append(f\"SHELL: {item['path']} - caps: {','.join(item.get('capabilities', []))} - {item.get('description', '')[:80]}\")
    elif t == 'python_script':
        summary.append(f\"PYTHON: {item['path']} - caps: {','.join(item.get('capabilities', []))} - {item.get('description', '')[:80]}\")
    elif t == 'cron_job':
        summary.append(f\"CRON: [{item.get('schedule', '')}] {item.get('command', '')[:100]}\")
    elif t == 'systemd_service':
        summary.append(f\"SERVICE: {item['name']} ({item.get('status', '')}) - {item.get('description', '')[:80]}\")
    elif t == 'docker_compose':
        summary.append(f\"COMPOSE: {item['directory']} - services: {','.join(item.get('services', []))}\")
    elif t == 'n8n_workflow':
        summary.append(f\"N8N: {item.get('name', '')} ({'active' if item.get('active') else 'inactive'}) - {item.get('nodes', 0)} nodes\")

print('\n'.join(summary[:80]))  # Limit to 80 items for context window
" 2>/dev/null)

    local analysis
    analysis=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
summary = open('/dev/stdin').read() if False else '''${summary}'''
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are analyzing a homelab automation landscape. Categorize what exists, identify overlaps and gaps. Output JSON with: {\"categories\": {\"monitoring\": [...paths], \"self_healing\": [...], \"backup\": [...], \"cleanup\": [...], \"llm_management\": [...], \"deployment\": [...], \"networking\": [...], \"other\": [...]}, \"gaps\": [\"description of missing automation\"], \"overlaps\": [\"description of duplicate/redundant automations\"], \"recommendations\": [\"what AgentHarness should wrap vs build new\"]}. Be specific about file paths.'},
        {'role': 'user', 'content': summary}
    ],
    'max_tokens': 1000,
    'temperature': 0.2
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    content = d['choices'][0]['message']['content'].strip()
    if content.startswith('\`\`\`'):
        content = content.split('\n', 1)[1].rsplit('\`\`\`', 1)[0].strip()
    # Try to parse as JSON
    analysis = json.loads(content)
    print(json.dumps(analysis, indent=2))
except:
    print(content)
" 2>/dev/null || echo '{"error": "LLM analysis failed"}')

    # Save analysis
    python3 -c "
import json
catalog = json.load(open('${CATALOG}'))
try:
    catalog['llm_analysis'] = json.loads('''${analysis}''')
except:
    catalog['llm_analysis'] = {'raw': '''${analysis}'''[:1000]}
json.dump(catalog, open('${CATALOG}', 'w'), indent=2)
" 2>/dev/null

    log_ok "LLM analysis saved to catalog"
}

# =============================================================================
# Print human-readable summary
# =============================================================================
print_summary() {
    log_header "Automation Landscape"

    python3 << 'PYEOF'
import json

catalog = json.load(open("/opt/agentharness/automation_catalog.json"))
items = catalog["items"]

# Group by type
by_type = {}
for item in items:
    t = item.get("type", "unknown")
    by_type.setdefault(t, []).append(item)

for t in sorted(by_type.keys()):
    group = by_type[t]
    print(f"\n  [{t.upper().replace('_', ' ')}] ({len(group)})")
    for item in group[:10]:  # Show max 10 per type
        if t in ("shell_script", "python_script"):
            caps = ", ".join(item.get("capabilities", []))[:40]
            desc = item.get("description", "")[:50]
            print(f"    {item['path']}")
            if caps:
                print(f"      caps: {caps}")
            if desc:
                print(f"      desc: {desc}")
        elif t == "cron_job":
            print(f"    [{item.get('schedule', '')}] {item.get('command', '')[:70]}")
        elif t == "systemd_service":
            print(f"    {item['name']} ({item.get('status', '')}) - {item.get('description', '')[:50]}")
        elif t == "docker_compose":
            svcs = ", ".join(item.get("services", []))[:60]
            print(f"    {item['directory']} -> {svcs}")
        elif t == "n8n_workflow":
            status = "active" if item.get("active") else "inactive"
            print(f"    {item.get('name', '')} ({status}, {item.get('nodes', 0)} nodes)")
        elif t == "config_file":
            print(f"    {item['path']}")
    if len(group) > 10:
        print(f"    ... and {len(group) - 10} more")

# Print LLM analysis if available
analysis = catalog.get("llm_analysis", {})
if "gaps" in analysis:
    print("\n  [GAPS — What's Missing]")
    for gap in analysis["gaps"]:
        print(f"    - {gap}")

if "overlaps" in analysis:
    print("\n  [OVERLAPS — Redundant]")
    for overlap in analysis["overlaps"]:
        print(f"    - {overlap}")

if "recommendations" in analysis:
    print("\n  [RECOMMENDATIONS — Wrap vs Build]")
    for rec in analysis["recommendations"]:
        print(f"    - {rec}")

print(f"\n  Total: {catalog['total_items']} automations discovered")
print(f"  Catalog: /opt/agentharness/automation_catalog.json")
PYEOF
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "Automation Discovery"
    log_info "Scanning entire system for existing scripts, services, cron jobs,"
    log_info "Docker configs, n8n workflows, and automation configs..."
    echo ""

    ensure_dir /opt/agentharness

    assemble_catalog
    analyze_with_llm
    print_summary
}

main "$@"
