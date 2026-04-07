#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# discover_chaguli.sh — Discover the actual Chaguli agent architecture
#
# Finds: the agent directory, tools.py, config.yml, .env, data directory,
#        Docker container mounts, existing tools, existing agents, and
#        all paths needed for integration.
#
# Saves to: /opt/agentharness/chaguli_paths.env
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

CHAGULI_PATHS="/opt/agentharness/chaguli_paths.env"

main() {
    log_info "Discovering Chaguli agent architecture..."

    ensure_dir /opt/agentharness
    > "${CHAGULI_PATHS}"

    # --- Find the Chaguli container ---
    local container_name=""
    container_name=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "chaguli" | head -1 || true)

    if [ -n "${container_name}" ]; then
        echo "CHAGULI_CONTAINER=${container_name}" >> "${CHAGULI_PATHS}"
        local image
        image=$(docker inspect --format '{{.Config.Image}}' "${container_name}" 2>/dev/null || echo "unknown")
        echo "CHAGULI_IMAGE=${image}" >> "${CHAGULI_PATHS}"
        log_ok "Container: ${container_name} (${image})"

        # Extract mounts
        local app_mount data_mount
        app_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Source}}{{end}}{{end}}' "${container_name}" 2>/dev/null || echo "")
        data_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "${container_name}" 2>/dev/null || echo "")

        [ -n "${app_mount}" ] && echo "CHAGULI_APP_DIR=${app_mount}" >> "${CHAGULI_PATHS}" && log_ok "App dir: ${app_mount}"
        [ -n "${data_mount}" ] && echo "CHAGULI_DATA_DIR=${data_mount}" >> "${CHAGULI_PATHS}" && log_ok "Data dir: ${data_mount}"

        # Check for USB mount
        local usb_mount
        usb_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/mnt/usb"}}{{.Source}}{{end}}{{end}}' "${container_name}" 2>/dev/null || echo "")
        [ -n "${usb_mount}" ] && echo "USB_MOUNT=${usb_mount}" >> "${CHAGULI_PATHS}" && log_ok "USB mount: ${usb_mount}"

        # Check for docker.sock mount
        local has_docker_sock
        has_docker_sock=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}yes{{end}}{{end}}' "${container_name}" 2>/dev/null || echo "no")
        echo "CHAGULI_HAS_DOCKER_SOCK=${has_docker_sock}" >> "${CHAGULI_PATHS}"
    else
        log_warn "Chaguli container not found"
    fi

    # --- Find the project root (parent of chaguli app dir) ---
    local project_root=""
    if [ -n "${app_mount:-}" ]; then
        project_root=$(dirname "${app_mount}")
        echo "PROJECT_ROOT=${project_root}" >> "${CHAGULI_PATHS}"
        log_ok "Project root: ${project_root}"
    else
        # Search common locations
        for p in /home/*/openclaw /home/*/homelab /opt/openclaw /opt/chaguli; do
            if [ -d "$p" ] && [ -f "$p/chaguli/agent.py" ]; then
                project_root="$p"
                echo "PROJECT_ROOT=${project_root}" >> "${CHAGULI_PATHS}"
                log_ok "Project root: ${project_root}"
                break
            fi
        done
    fi

    if [ -z "${project_root}" ]; then
        log_error "Could not find Chaguli project root"
        return 1
    fi

    # --- Find key files ---
    local chaguli_dir="${project_root}/chaguli"

    # Core Python files
    for f in agent.py tools.py config.yml router.py memory.py self_improve.py \
             briefings.py heartbeat.py telegram_handler.py voice_profile.md \
             slop_gate.py approval.py webhook_server.py improvements.yml; do
        if [ -f "${chaguli_dir}/${f}" ]; then
            local varname
            varname=$(echo "CHAGULI_${f}" | tr '.' '_' | tr '[:lower:]' '[:upper:]' | tr '-' '_')
            echo "${varname}=${chaguli_dir}/${f}" >> "${CHAGULI_PATHS}"
        fi
    done

    # Domains and clients directories
    [ -d "${chaguli_dir}/domains" ] && echo "CHAGULI_DOMAINS_DIR=${chaguli_dir}/domains" >> "${CHAGULI_PATHS}"
    [ -d "${chaguli_dir}/clients" ] && echo "CHAGULI_CLIENTS_DIR=${chaguli_dir}/clients" >> "${CHAGULI_PATHS}"

    # --- Master .env ---
    local master_env=""
    for p in "${project_root}/.env" "${project_root}/chaguli/.env"; do
        if [ -f "$p" ]; then
            master_env="$p"
            echo "MASTER_ENV=${master_env}" >> "${CHAGULI_PATHS}"
            log_ok "Master .env: ${master_env}"
            break
        fi
    done

    # --- Existing tools ---
    log_info "Existing Chaguli tools:"
    if [ -f "${chaguli_dir}/tools.py" ]; then
        python3 -c "
import re
content = open('${chaguli_dir}/tools.py').read()
names = re.findall(r'\"name\":\s*\"(\w+)\"', content)
for n in names:
    print(f'  - {n}')
print(f'Total: {len(names)} tools')
" 2>/dev/null
        local tool_count
        tool_count=$(python3 -c "
import re
content = open('${chaguli_dir}/tools.py').read()
print(len(re.findall(r'\"name\":\s*\"(\w+)\"', content)))
" 2>/dev/null || echo "0")
        echo "CHAGULI_TOOL_COUNT=${tool_count}" >> "${CHAGULI_PATHS}"
    fi

    # --- Other agents ---
    log_info "Other agents:"
    for agent_dir in "${project_root}"/*/; do
        local name
        name=$(basename "${agent_dir}")
        if [ -f "${agent_dir}/agent.py" ] || [ -f "${agent_dir}/main.py" ] || \
           docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${name}"; then
            log_info "  ${name}"
        fi
    done

    # --- Scripts directory ---
    if [ -d "${project_root}/scripts" ]; then
        echo "PROJECT_SCRIPTS_DIR=${project_root}/scripts" >> "${CHAGULI_PATHS}"
        log_info "Existing scripts:"
        ls "${project_root}/scripts/" 2>/dev/null | while read -r f; do
            log_info "  ${f}"
        done
    fi

    # --- Existing benchmark/LLM files ---
    local home_dir
    home_dir=$(dirname "${project_root}")
    for f in apply_best_llm.sh llm_benchmark_results.txt llm_bench_v2.log llm_compare_output.log; do
        if [ -f "${home_dir}/${f}" ]; then
            echo "EXISTING_$(echo ${f} | tr '.' '_' | tr '[:lower:]' '[:upper:]')=${home_dir}/${f}" >> "${CHAGULI_PATHS}"
            log_ok "Found: ${home_dir}/${f}"
        fi
    done

    # --- Built inference engines ---
    for dir in "${home_dir}/ik_llama.cpp" "${home_dir}/llama.cpp"; do
        if [ -d "${dir}" ] && [ -f "${dir}/build/bin/llama-server" ]; then
            local name
            name=$(basename "${dir}")
            echo "EXISTING_$(echo ${name} | tr '.' '_' | tr '[:lower:]' '[:upper:]')_DIR=${dir}" >> "${CHAGULI_PATHS}"
            log_ok "Built: ${dir}"
        fi
    done

    # --- Config from config.yml ---
    if [ -f "${chaguli_dir}/config.yml" ]; then
        python3 -c "
import yaml
cfg = yaml.safe_load(open('${chaguli_dir}/config.yml'))

# LLM config
llm = cfg.get('llm', {})
print(f'LLM_LOCAL_URL={llm.get(\"local_url\", \"\")}')
print(f'LLM_GROQ_MODEL={llm.get(\"groq_model\", \"\")}')
print(f'LLM_GROQ_DAILY_CAP={llm.get(\"groq_daily_cap\", 200)}')

# Monitored services
services = cfg.get('services', {}).get('monitor', [])
print(f'MONITORED_SERVICES={\" \".join(services)}')

# Disk paths
disk_paths = cfg.get('disk', {}).get('paths', [])
print(f'DISK_PATHS={\" \".join(disk_paths)}')

# Guardrails
guardrails = cfg.get('guardrails', {})
notify = guardrails.get('notify_restart_containers', [])
confirm = guardrails.get('confirm_restart_containers', [])
print(f'GUARDRAIL_NOTIFY_RESTART={\" \".join(notify)}')
print(f'GUARDRAIL_CONFIRM_RESTART={\" \".join(confirm)}')
" 2>/dev/null >> "${CHAGULI_PATHS}"
    fi

    log_ok "Chaguli discovery complete: ${CHAGULI_PATHS}"
}

main "$@"
