#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# validate.sh — Post-install validation of all AgentHarness components
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local result="$2"
    local detail="${3:-}"

    if [ "${result}" = "ok" ]; then
        printf "  %-40s %s\n" "${name}" "$(tput setaf 2)OK$(tput sgr0) ${detail}"
        ((PASS++))
    elif [ "${result}" = "warn" ]; then
        printf "  %-40s %s\n" "${name}" "$(tput setaf 3)WARN$(tput sgr0) ${detail}"
        ((WARN++))
    else
        printf "  %-40s %s\n" "${name}" "$(tput setaf 1)FAIL$(tput sgr0) ${detail}"
        ((FAIL++))
    fi
}

# -----------------------------------------------------------------------------
main() {
    log_header "AgentHarness Validation"
    echo ""

    # --- Section 1: Inference Engines ---
    echo "  [Inference Engines]"

    if command -v ik-llama-server &>/dev/null; then
        local ik_ver
        ik_ver=$(ik-llama-server --version 2>&1 | head -1 || echo "unknown")
        check "ik_llama.cpp" "ok" "${ik_ver}"
    else
        check "ik_llama.cpp" "fail" "not found in PATH"
    fi

    if command -v llama-server &>/dev/null; then
        local stock_ver
        stock_ver=$(llama-server --version 2>&1 | head -1 || echo "unknown")
        check "stock llama.cpp" "ok" "${stock_ver}"
    else
        check "stock llama.cpp" "warn" "not built (optional, needed for benchmarking)"
    fi

    echo ""

    # --- Section 2: Models ---
    echo "  [Models]"

    if [ -f "${AH_DATA_DIR}/model_catalog.json" ]; then
        local model_count
        model_count=$(python3 -c "import json; print(len(json.load(open('${AH_DATA_DIR}/model_catalog.json'))))" 2>/dev/null || echo "0")
        check "Model catalog" "ok" "${model_count} model(s)"
    else
        check "Model catalog" "fail" "model_catalog.json not found"
    fi

    for model_dir in /opt/models/*/; do
        local model_name
        model_name=$(basename "${model_dir}")
        local gguf_count
        gguf_count=$(find "${model_dir}" -name "*.gguf" -type f 2>/dev/null | wc -l)
        if [ "${gguf_count}" -gt 0 ]; then
            local gguf_size
            gguf_size=$(du -sh "${model_dir}" 2>/dev/null | cut -f1)
            check "Model: ${model_name}" "ok" "${gguf_count} file(s), ${gguf_size}"
        else
            check "Model: ${model_name}" "fail" "no GGUF files found"
        fi
    done

    echo ""

    # --- Section 3: LLM Server ---
    echo "  [LLM Server]"

    if curl -sf http://localhost:8080/health >/dev/null 2>&1; then
        local slots
        slots=$(curl -sf http://localhost:8080/slots 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} slot(s)')" 2>/dev/null || echo "running")
        check "Primary server (port 8080)" "ok" "${slots}"
    else
        check "Primary server (port 8080)" "warn" "not running (start with: sudo systemctl start llama-primary)"
    fi

    if curl -sf http://localhost:8081/health >/dev/null 2>&1; then
        check "Fast server (port 8081)" "ok" "running"
    else
        check "Fast server (port 8081)" "warn" "not running (optional)"
    fi

    echo ""

    # --- Section 4: SearXNG ---
    echo "  [Web Search]"

    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q searxng; then
        # Try an actual search
        local search_ok
        search_ok=$(curl -sf "http://localhost:8118/search?q=test&format=json" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('results',[])))" 2>/dev/null || echo "0")
        if [ "${search_ok}" -gt 0 ]; then
            check "SearXNG" "ok" "running, ${search_ok} results for test query"
        else
            check "SearXNG" "warn" "container running but search returned 0 results"
        fi
    else
        check "SearXNG" "warn" "not running (start with: cd /opt/searxng && docker compose up -d)"
    fi

    echo ""

    # --- Section 5: Coding Assistants ---
    echo "  [Coding Assistants]"

    if command -v opencode &>/dev/null; then
        check "OpenCode" "ok" "$(opencode --version 2>&1 | head -1 || echo 'installed')"
    else
        check "OpenCode" "warn" "not installed (optional)"
    fi

    if command -v aider &>/dev/null; then
        check "Aider" "ok" "$(aider --version 2>&1 | head -1 || echo 'installed')"
    else
        check "Aider" "warn" "not installed (optional)"
    fi

    echo ""

    # --- Section 6: Python Agent Framework ---
    echo "  [Agent Framework]"

    if python3 -c "import smolagents" 2>/dev/null; then
        local smol_ver
        smol_ver=$(python3 -c "import smolagents; print(smolagents.__version__)" 2>/dev/null || echo "installed")
        check "smolagents" "ok" "v${smol_ver}"
    else
        check "smolagents" "warn" "not installed (pip install smolagents)"
    fi

    echo ""

    # --- Section 7: System Services ---
    echo "  [Systemd Services]"

    for svc in llama-primary llama-fast agentharness-weekly; do
        if systemctl is-enabled "${svc}" &>/dev/null; then
            local status
            status=$(systemctl is-active "${svc}" 2>/dev/null || echo "inactive")
            if [ "${status}" = "active" ]; then
                check "Service: ${svc}" "ok" "enabled, running"
            else
                check "Service: ${svc}" "warn" "enabled, ${status}"
            fi
        else
            check "Service: ${svc}" "warn" "not enabled"
        fi
    done

    echo ""

    # --- Section 8: Docker Services ---
    echo "  [Docker Containers]"
    docker ps --format "{{.Names}}\t{{.Status}}" 2>/dev/null | while IFS=$'\t' read -r name status; do
        if echo "${status}" | grep -q "Up"; then
            check "Container: ${name}" "ok" "${status}"
        else
            check "Container: ${name}" "warn" "${status}"
        fi
    done

    echo ""

    # --- Section 9: Hardware Profile ---
    echo "  [Hardware]"

    if [ -f "${AH_DATA_DIR}/hw_profile.env" ]; then
        source "${AH_DATA_DIR}/hw_profile.env"
        check "Hardware profile" "ok" "${CPU_MODEL}, ${TOTAL_RAM_GB}GB RAM"
    else
        check "Hardware profile" "fail" "not detected (run build_inference.sh first)"
    fi

    local swap_used
    swap_used=$(free -m | awk '/Swap/ {print $3}')
    if [ "${swap_used}" -lt 100 ]; then
        check "Swap usage" "ok" "${swap_used}MB used"
    else
        check "Swap usage" "warn" "${swap_used}MB used — LLM may be swapping, reduce model size or context"
    fi

    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [ "${disk_pct}" -lt 80 ]; then
        check "Disk usage" "ok" "${disk_pct}% used"
    elif [ "${disk_pct}" -lt 90 ]; then
        check "Disk usage" "warn" "${disk_pct}% used — getting full"
    else
        check "Disk usage" "fail" "${disk_pct}% used — critical!"
    fi

    # --- Summary ---
    echo ""
    log_header "Validation Summary"
    echo ""
    echo "  $(tput setaf 2)PASS: ${PASS}$(tput sgr0)  |  $(tput setaf 3)WARN: ${WARN}$(tput sgr0)  |  $(tput setaf 1)FAIL: ${FAIL}$(tput sgr0)"
    echo ""

    if [ "${FAIL}" -gt 0 ]; then
        log_error "Some checks failed. Review the output above."
        return 1
    elif [ "${WARN}" -gt 0 ]; then
        log_warn "Some optional components are missing. Review the output above."
        return 0
    else
        log_ok "All checks passed!"
        return 0
    fi
}

main "$@"
