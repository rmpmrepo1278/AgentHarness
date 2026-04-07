#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# doctor.sh — Diagnose what's wrong and suggest fixes
#
# Run this when something breaks. It checks every component, identifies
# the specific failure, explains WHY it failed in plain English, and
# gives you the exact command to fix it.
#
# Usage:
#   ./scripts/doctor.sh              # Full diagnosis
#   ./scripts/doctor.sh --fix        # Diagnose AND auto-fix safe issues
#   ./scripts/doctor.sh phase N      # Diagnose a specific install phase
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

AUTO_FIX=false
[[ "${1:-}" == "--fix" ]] && AUTO_FIX=true

ISSUES=()
FIXES=()

# =============================================================================
# Helpers
# =============================================================================
diagnose() {
    local component="$1"
    local status="$2"  # ok, warn, fail
    local message="$3"
    local fix="${4:-}"

    case "${status}" in
        ok)   printf "  ${GREEN}✓${NC} %-40s %s\n" "${component}" "${message}" ;;
        warn) printf "  ${YELLOW}⚠${NC} %-40s %s\n" "${component}" "${message}"
              [ -n "${fix}" ] && ISSUES+=("${component}: ${message}") && FIXES+=("${fix}") ;;
        fail) printf "  ${RED}✗${NC} %-40s %s\n" "${component}" "${message}"
              [ -n "${fix}" ] && ISSUES+=("${component}: ${message}") && FIXES+=("${fix}") ;;
    esac
}

try_fix() {
    local description="$1"
    local command="$2"

    if [ "${AUTO_FIX}" = true ]; then
        printf "  ${BLUE}→ Auto-fixing:${NC} %s\n" "${description}"
        if eval "${command}" 2>&1; then
            printf "  ${GREEN}  Fixed!${NC}\n"
            return 0
        else
            printf "  ${RED}  Fix failed. Try manually:${NC} %s\n" "${command}"
            return 1
        fi
    fi
}

section() {
    echo ""
    printf "${BOLD}━━━ %s ━━━${NC}\n" "$1"
    echo ""
}

# =============================================================================
# Phase 0: Prerequisites
# =============================================================================
check_prerequisites() {
    section "Prerequisites"

    # OS
    if [ -f /etc/debian_version ]; then
        diagnose "Operating system" "ok" "Debian $(cat /etc/debian_version)"
    else
        diagnose "Operating system" "warn" "Not Debian — some commands may differ" ""
    fi

    # RAM
    local ram_gb
    ram_gb=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "0")
    if [ "${ram_gb}" -ge 16 ]; then
        diagnose "RAM" "ok" "${ram_gb}GB"
    elif [ "${ram_gb}" -ge 8 ]; then
        diagnose "RAM" "warn" "${ram_gb}GB — may be tight for large models" ""
    else
        diagnose "RAM" "fail" "${ram_gb}GB — insufficient for LLM inference" ""
    fi

    # Disk space
    local disk_avail_gb
    disk_avail_gb=$(df / | awk 'NR==2 {printf "%.0f", $4/1024/1024}')
    if [ "${disk_avail_gb}" -ge 50 ]; then
        diagnose "Disk space" "ok" "${disk_avail_gb}GB free"
    elif [ "${disk_avail_gb}" -ge 20 ]; then
        diagnose "Disk space" "warn" "${disk_avail_gb}GB free — may need cleanup for models" \
            "bash ${SCRIPT_DIR}/cleanup.sh"
    else
        diagnose "Disk space" "fail" "${disk_avail_gb}GB free — not enough for models" \
            "bash ${SCRIPT_DIR}/cleanup.sh"
    fi

    # Required tools
    for tool in git cmake make gcc python3 pip3 curl docker; do
        if command -v "${tool}" &>/dev/null; then
            diagnose "${tool}" "ok" "$(command -v ${tool})"
        else
            local fix="sudo apt-get install -y ${tool}"
            [ "${tool}" = "pip3" ] && fix="sudo apt-get install -y python3-pip"
            [ "${tool}" = "gcc" ] && fix="sudo apt-get install -y build-essential"
            [ "${tool}" = "cmake" ] && fix="sudo apt-get install -y cmake"
            diagnose "${tool}" "fail" "not installed" "${fix}"
            try_fix "Installing ${tool}" "${fix}"
        fi
    done

    # Docker running
    if docker info &>/dev/null; then
        diagnose "Docker daemon" "ok" "running"
    else
        diagnose "Docker daemon" "fail" "not running or no permission" \
            "sudo systemctl start docker && sudo usermod -aG docker \$USER"
    fi

    # Python packages
    for pkg in yaml huggingface_hub; do
        if python3 -c "import ${pkg}" 2>/dev/null; then
            diagnose "Python: ${pkg}" "ok" "installed"
        else
            local pip_name="${pkg}"
            [ "${pkg}" = "yaml" ] && pip_name="pyyaml"
            [ "${pkg}" = "huggingface_hub" ] && pip_name="huggingface_hub"
            diagnose "Python: ${pkg}" "warn" "not installed" \
                "pip install ${pip_name}"
            try_fix "Installing ${pip_name}" "pip install --quiet ${pip_name}"
        fi
    done
}

# =============================================================================
# Phase 1: Inference Engines
# =============================================================================
check_inference() {
    section "Inference Engines"

    for prefix in ik-llama llama; do
        local server="${prefix}-server"
        local bench="${prefix}-bench"

        if command -v "${server}" &>/dev/null; then
            diagnose "${server}" "ok" "$(command -v ${server})"
        else
            # Check if built but not linked
            local build_dir="/opt/${prefix//-/_}/build/bin/llama-server"
            [ "${prefix}" = "ik-llama" ] && build_dir="/opt/ik_llama/build/bin/llama-server"
            [ "${prefix}" = "llama" ] && build_dir="/opt/llama.cpp/build/bin/llama-server"

            if [ -f "${build_dir}" ]; then
                diagnose "${server}" "warn" "built but not in PATH" \
                    "sudo ln -sf ${build_dir} /usr/local/bin/${server}"
                try_fix "Linking ${server}" "sudo ln -sf ${build_dir} /usr/local/bin/${server}"
            else
                diagnose "${server}" "warn" "not built" \
                    "bash ${SCRIPT_DIR}/build_inference.sh"
            fi
        fi
    done

    # Check if a server is actually running
    if curl -sf --max-time 3 http://localhost:8080/health &>/dev/null; then
        diagnose "LLM server :8080" "ok" "healthy"
    elif curl -sf --max-time 3 http://localhost:8081/health &>/dev/null; then
        diagnose "LLM server :8081" "ok" "healthy (fast model)"
    else
        # Check systemd
        if systemctl is-active llama-primary &>/dev/null; then
            diagnose "LLM server" "warn" "systemd says active but not responding" \
                "sudo journalctl -u llama-primary --no-pager -n 20"
        elif systemctl is-enabled llama-primary &>/dev/null; then
            diagnose "LLM server" "fail" "enabled but not running" \
                "sudo systemctl start llama-primary"
            try_fix "Starting LLM server" "sudo systemctl start llama-primary"
        else
            diagnose "LLM server" "warn" "not configured as systemd service" \
                "Check: ls /etc/systemd/system/llama-*.service"
        fi
    fi
}

# =============================================================================
# Phase 2: Models
# =============================================================================
check_models() {
    section "Models"

    local model_count
    model_count=$(find /opt/models -name "*.gguf" -type f 2>/dev/null | wc -l)

    if [ "${model_count}" -gt 0 ]; then
        diagnose "GGUF models" "ok" "${model_count} model(s) found"
        find /opt/models -name "*.gguf" -type f 2>/dev/null | while read -r m; do
            local size
            size=$(du -h "${m}" | cut -f1)
            local name
            name=$(basename "$(dirname "${m}")")/$(basename "${m}")
            diagnose "  ${name}" "ok" "${size}"
        done
    else
        diagnose "GGUF models" "fail" "no models found in /opt/models" \
            "bash ${SCRIPT_DIR}/download_models.sh"
    fi

    # Model catalog
    if [ -f "${AH_DATA_DIR}/model_catalog.json" ]; then
        diagnose "Model catalog" "ok" "exists"
    else
        diagnose "Model catalog" "warn" "missing — run download_models.sh" \
            "bash ${SCRIPT_DIR}/download_models.sh"
    fi

    # Check if systemd service points to a valid model
    if [ -f /etc/systemd/system/llama-primary.service ]; then
        local model_path
        model_path=$(grep -oP '(?<=--model )\S+' /etc/systemd/system/llama-primary.service 2>/dev/null || echo "")
        if [ -n "${model_path}" ] && [ -f "${model_path}" ]; then
            diagnose "Service model path" "ok" "$(basename ${model_path})"
        elif [ -n "${model_path}" ]; then
            diagnose "Service model path" "fail" "${model_path} does not exist" \
                "Update the model path in /etc/systemd/system/llama-primary.service and run: sudo systemctl daemon-reload"
        fi
    fi
}

# =============================================================================
# Phase 3: SearXNG
# =============================================================================
check_searxng() {
    section "SearXNG"

    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q searxng; then
        diagnose "SearXNG container" "ok" "running"

        if curl -sf --max-time 5 "http://localhost:8888/search?q=test&format=json" &>/dev/null; then
            diagnose "SearXNG search" "ok" "returning results"
        else
            diagnose "SearXNG search" "warn" "container running but search failing" \
                "docker logs searxng --tail 20"
        fi
    elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q searxng; then
        diagnose "SearXNG container" "fail" "exists but stopped" \
            "cd /opt/searxng && docker compose up -d"
        try_fix "Starting SearXNG" "cd /opt/searxng && docker compose up -d"
    else
        diagnose "SearXNG" "warn" "not deployed" \
            "Phase 4 of install.sh deploys it"
    fi
}

# =============================================================================
# Phase 4: OpenClaw / Chaguli
# =============================================================================
check_openclaw() {
    section "OpenClaw / Chaguli"

    if [ -f "${AH_DATA_DIR}/openclaw_paths.env" ]; then
        source "${AH_DATA_DIR}/openclaw_paths.env"
        diagnose "OpenClaw discovery" "ok" "paths discovered"

        [ -n "${OPENCLAW_BIN:-}" ] && diagnose "Binary" "ok" "${OPENCLAW_BIN}" || \
            diagnose "Binary" "warn" "not found in PATH" ""

        [ -d "${OPENCLAW_WORKSPACE:-}" ] && diagnose "Workspace" "ok" "${OPENCLAW_WORKSPACE}" || \
            diagnose "Workspace" "warn" "not found" ""

        [ -d "${OPENCLAW_SKILLS_DIR:-}" ] && diagnose "Skills dir" "ok" "${OPENCLAW_SKILLS_DIR}" || \
            diagnose "Skills dir" "warn" "not found" ""

        [ -f "${OPENCLAW_AGENTS_MD:-}" ] && diagnose "AGENTS.md" "ok" "${OPENCLAW_AGENTS_MD}" || \
            diagnose "AGENTS.md" "warn" "not found" ""

        [ "${OPENCLAW_GATEWAY_RUNNING:-false}" = "true" ] && \
            diagnose "Gateway" "ok" "running" || \
            diagnose "Gateway" "warn" "not detected as running" ""
    else
        diagnose "OpenClaw discovery" "fail" "not yet run" \
            "bash ${SCRIPT_DIR}/discover_automations.sh"
    fi
}

# =============================================================================
# Phase 5: AgentHarness State
# =============================================================================
check_agentharness() {
    section "AgentHarness State"

    for f in \
        "${AH_DATA_DIR}/.env|Environment config" \
        "${AH_DATA_DIR}/hw_profile.env|Hardware profile" \
        "${AH_DATA_DIR}/openclaw_paths.env|OpenClaw paths" \
        "${AH_DATA_DIR}/automation_catalog.json|Automation catalog" \
        "${AH_DATA_DIR}/service_registry.json|Service registry" \
        "${AH_DATA_DIR}/model_catalog.json|Model catalog" \
        "${AH_DATA_DIR}/benchmark_results.json|Benchmark results" \
        "${AH_DATA_DIR}/chaguli_memory.json|Chaguli memory"; do

        local path desc
        IFS='|' read -r path desc <<< "${f}"

        if [ -f "${path}" ]; then
            local age_h
            age_h=$(( ($(date +%s) - $(stat -c %Y "${path}" 2>/dev/null || echo "0")) / 3600 ))
            diagnose "${desc}" "ok" "${age_h}h old"
        else
            diagnose "${desc}" "warn" "missing" ""
        fi
    done

    # Scheduler
    if crontab -l 2>/dev/null | grep -q "scheduler.sh"; then
        diagnose "Scheduler cron" "ok" "installed"
    else
        diagnose "Scheduler cron" "warn" "not installed" \
            "Run the setup_scheduler phase of install.sh"
    fi

    # Registry
    if [ -f "${AH_CONFIG_DIR}/harness_registry.yaml" ]; then
        diagnose "Plugin registry" "ok" "exists"
    else
        diagnose "Plugin registry" "warn" "missing" \
            "cp ${PROJECT_DIR}/config/harness_registry.yaml ${AH_CONFIG_DIR}/"
    fi
}

# =============================================================================
# Phase 6: Storage / Backup
# =============================================================================
check_storage() {
    section "Backup Storage"

    if [ -f "${AH_DATA_DIR}/storage_paths.env" ]; then
        source "${AH_DATA_DIR}/storage_paths.env"
        if [ -n "${BACKUP_DRIVE:-}" ] && [ -d "${BACKUP_DRIVE}" ]; then
            local avail
            avail=$(df -h "${BACKUP_DRIVE}" 2>/dev/null | awk 'NR==2 {print $4}')
            diagnose "Backup drive" "ok" "${BACKUP_DRIVE} (${avail} free)"
        else
            diagnose "Backup drive" "warn" "discovered path not mounted" \
                "Check if USB drive is connected: lsblk"
        fi
    else
        diagnose "Storage discovery" "warn" "not run" \
            "bash ${SCRIPT_DIR}/discover_storage.sh"
    fi
}

# =============================================================================
# Network
# =============================================================================
check_network() {
    section "Network"

    if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        diagnose "Internet" "ok" "reachable"
    else
        diagnose "Internet" "warn" "offline (expected during 11PM-7AM)" ""
    fi

    if [ -n "${MINIPC_IP:-}" ]; then
        if ping -c 1 -W 2 "${MINIPC_IP}" &>/dev/null; then
            diagnose "Mini PC" "ok" "${MINIPC_IP} reachable"
        else
            diagnose "Mini PC" "warn" "${MINIPC_IP} unreachable" ""
        fi
    fi
}

# =============================================================================
# Recent errors in logs
# =============================================================================
check_logs() {
    section "Recent Errors"

    # Scheduler log
    local sched_log="${AH_LOGS_DIR}/scheduler.log"
    if [ -f "${sched_log}" ]; then
        local recent_errors
        recent_errors=$(tail -100 "${sched_log}" 2>/dev/null | grep -i "error\|fail\|exception" | tail -5)
        if [ -n "${recent_errors}" ]; then
            diagnose "Scheduler errors" "warn" "recent errors found" ""
            echo "${recent_errors}" | while read -r line; do
                printf "    ${YELLOW}%s${NC}\n" "${line:0:120}"
            done
        else
            diagnose "Scheduler log" "ok" "no recent errors"
        fi
    else
        diagnose "Scheduler log" "warn" "not found (scheduler hasn't run yet)" ""
    fi

    # systemd journal for llama services
    for svc in llama-primary llama-fast; do
        if systemctl is-enabled "${svc}" &>/dev/null; then
            local errors
            errors=$(journalctl -u "${svc}" --no-pager -n 10 --since "1 hour ago" 2>/dev/null | \
                grep -i "error\|fail\|crash\|oom\|killed" | tail -3)
            if [ -n "${errors}" ]; then
                diagnose "${svc} journal" "warn" "errors in last hour" ""
                echo "${errors}" | while read -r line; do
                    printf "    ${YELLOW}%s${NC}\n" "${line:0:120}"
                done
            fi
        fi
    done
}

# =============================================================================
# Summary + Fix suggestions
# =============================================================================
print_summary() {
    section "Summary"

    if [ ${#ISSUES[@]} -eq 0 ]; then
        printf "  ${GREEN}${BOLD}Everything looks healthy!${NC}\n\n"
        return
    fi

    printf "  ${RED}${BOLD}Found ${#ISSUES[@]} issue(s):${NC}\n\n"

    for i in "${!ISSUES[@]}"; do
        local issue="${ISSUES[$i]}"
        local fix="${FIXES[$i]}"
        printf "  ${YELLOW}%d.${NC} %s\n" "$((i+1))" "${issue}"
        if [ -n "${fix}" ]; then
            printf "     ${BLUE}Fix:${NC} %s\n" "${fix}"
        fi
        echo ""
    done

    if [ "${AUTO_FIX}" = false ] && [ ${#FIXES[@]} -gt 0 ]; then
        echo ""
        printf "  ${BOLD}Run with --fix to auto-fix safe issues:${NC}\n"
        echo "    bash ${SCRIPT_DIR}/doctor.sh --fix"
        echo ""
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo ""
    printf "${BOLD}╔═══════════════════════════════════════╗${NC}\n"
    printf "${BOLD}║     AgentHarness Doctor               ║${NC}\n"
    printf "${BOLD}╚═══════════════════════════════════════╝${NC}\n"

    [ "${AUTO_FIX}" = true ] && printf "\n  ${BLUE}Auto-fix mode enabled${NC}\n"

    if [ "${1:-}" = "phase" ] && [ -n "${2:-}" ]; then
        case "${2}" in
            0|prereq*)    check_prerequisites ;;
            1|infer*)     check_inference ;;
            2|model*)     check_models ;;
            3|searx*)     check_searxng ;;
            4|openclaw*)  check_openclaw ;;
            5|state*)     check_agentharness ;;
            6|storage*)   check_storage ;;
            7|network*)   check_network ;;
            8|log*)       check_logs ;;
            *)            echo "Unknown phase: ${2}. Use 0-8 or name." ;;
        esac
    else
        check_prerequisites
        check_inference
        check_models
        check_searxng
        check_openclaw
        check_agentharness
        check_storage
        check_network
        check_logs
    fi

    print_summary
}

main "$@"
