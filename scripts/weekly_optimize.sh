#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# weekly_optimize.sh — Weekly search for new models, tools, techniques
#                      Generates a recommendation report and optionally
#                      auto-downloads promising new models
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REPORT_DIR="/opt/agentharness/reports"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"
WEEKLY_REPORT="/opt/agentharness/reports/weekly_$(timestamp).md"

# Load environment
[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/hw_profile.env ] && source /opt/agentharness/hw_profile.env
[ -f /opt/agentharness/best_config.env ] && source /opt/agentharness/best_config.env

# -----------------------------------------------------------------------------
# Search SearXNG and return results as JSON
# -----------------------------------------------------------------------------
search() {
    local query="$1"
    local max_results="${2:-10}"

    curl -sf "${SEARXNG_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${query}'))")&format=json&engines=google,duckduckgo,github" \
        2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    results = data.get('results', [])[:${max_results}]
    for r in results:
        print(json.dumps({'title': r.get('title',''), 'url': r.get('url',''), 'content': r.get('content','')[:200]}))
except:
    pass
" 2>/dev/null
}

# -----------------------------------------------------------------------------
# Ask local LLM to analyze search results
# -----------------------------------------------------------------------------
ask_llm() {
    local prompt="$1"
    local max_tokens="${2:-500}"

    local response
    response=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are a concise technical analyst for a homelab running on a Ryzen 4700U with 36GB RAM (4+32 mismatched DDR4). Focus on practical recommendations for CPU-only LLM inference. Current setup: ${BEST_MODEL:-unknown} on ${BEST_ENGINE:-ik-llama}. Be specific about model names, sizes, and expected performance.'},
        {'role': 'user', 'content': '''${prompt}'''}
    ],
    'max_tokens': ${max_tokens},
    'temperature': 0.3
}))
" 2>/dev/null)" 2>/dev/null) || { echo "(LLM unavailable — raw results only)"; return; }

    echo "${response}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except:
    print('(Failed to parse LLM response)')
" 2>/dev/null
}

# -----------------------------------------------------------------------------
# Search categories
# -----------------------------------------------------------------------------
search_new_models() {
    log_info "Searching for new LLM models..."

    local queries=(
        "new GGUF models 2026 llama.cpp small MoE tool calling"
        "best local LLM models homelab CPU inference $(date +%Y) site:reddit.com/r/LocalLLaMA"
        "Qwen Gemma Mistral new model release $(date +%B) $(date +%Y) GGUF"
        "MoE model small active parameters CPU inference $(date +%Y)"
        "best function calling local LLM model $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## New Models" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    # Deduplicate and format
    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -20)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url content
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    # LLM analysis
    local analysis
    analysis=$(ask_llm "Based on these search results about new LLM models, identify any models that could improve our setup. Current: ${BEST_MODEL:-Qwen3.5-35B-A3B}. Requirements: fits in 36GB RAM, CPU-only, good tool calling. Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

search_new_engines() {
    log_info "Searching for inference engine updates..."

    local queries=(
        "ik_llama.cpp update CPU performance $(date +%Y)"
        "llama.cpp new release CPU optimization MoE $(date +%Y)"
        "local LLM inference engine faster than llama.cpp CPU $(date +%Y)"
        "PowerInfer MoE CPU inference update $(date +%Y)"
        "speculative decoding CPU improvements $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## Inference Engines & Optimization" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -15)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    local analysis
    analysis=$(ask_llm "Any new inference engine updates or optimization techniques that could speed up our CPU inference? Current: ${BEST_ENGINE:-ik-llama}. We have AVX2 (no AVX-512). Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

search_techniques() {
    log_info "Searching for new techniques and optimizations..."

    local queries=(
        "KV cache optimization llama.cpp CPU $(date +%Y)"
        "quantization technique GGUF improvement $(date +%Y)"
        "prompt caching local LLM optimization $(date +%Y)"
        "homelab AI agent best practices $(date +%Y) site:reddit.com"
        "MCP server new tools Docker homelab $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## Techniques & Best Practices" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -15)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    local analysis
    analysis=$(ask_llm "Any new techniques for optimizing local LLM performance on CPU? Focus on practical things we can apply to our setup. Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

# -----------------------------------------------------------------------------
# Generate action items
# -----------------------------------------------------------------------------
generate_action_items() {
    log_info "Generating action items..."

    echo "## Action Items" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    # Ask LLM to summarize the full report into action items
    local report_content
    report_content=$(cat "${WEEKLY_REPORT}")

    local actions
    actions=$(ask_llm "Based on this weekly optimization report, list the top 3-5 concrete action items, prioritized by impact. For each, specify: what to do, expected improvement, and effort (low/medium/high). Only recommend actions with clear evidence of improvement. Report: ${report_content}" 800)

    echo "${actions}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

# -----------------------------------------------------------------------------
# Check for engine updates (git pull)
# -----------------------------------------------------------------------------
check_engine_updates() {
    log_info "Checking for engine updates..."

    echo "## Engine Update Status" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    for dir in /opt/ik_llama /opt/llama.cpp; do
        if [ -d "${dir}/.git" ]; then
            local name
            name=$(basename "${dir}")
            cd "${dir}"
            local current_hash
            current_hash=$(git rev-parse --short HEAD)
            local behind
            behind=$(git fetch --dry-run 2>&1 | wc -l)

            if [ "${behind}" -gt 0 ]; then
                local latest
                latest=$(git ls-remote --heads origin main 2>/dev/null | cut -c1-7 || echo "unknown")
                echo "- **${name}**: Current ${current_hash}, remote has updates. Consider rebuilding." >> "${WEEKLY_REPORT}"
            else
                echo "- **${name}**: Up to date (${current_hash})" >> "${WEEKLY_REPORT}"
            fi
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"
}

# -----------------------------------------------------------------------------
# Send notification (if Telegram configured)
# -----------------------------------------------------------------------------
notify() {
    local message="$1"

    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sf "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${message}" \
            -d "parse_mode=Markdown" \
            &>/dev/null || true
    fi
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    log_header "Weekly Optimization Scan"

    ensure_dir "${REPORT_DIR}"

    # Check SearXNG is available
    if ! curl -sf "${SEARXNG_URL}/healthz" &>/dev/null && \
       ! curl -sf "${SEARXNG_URL}/search?q=test&format=json" &>/dev/null; then
        log_error "SearXNG not reachable at ${SEARXNG_URL}. Start it first."
        return 1
    fi

    # Initialize report
    cat > "${WEEKLY_REPORT}" << EOF
# AgentHarness Weekly Optimization Report
**Date**: $(date '+%Y-%m-%d %H:%M')
**Current Setup**: ${BEST_MODEL:-unknown} on ${BEST_ENGINE:-unknown} (score: ${BEST_COMPOSITE:-N/A}/10)
**Hardware**: ${CPU_MODEL:-unknown}, ${TOTAL_RAM_GB:-36}GB RAM

---

EOF

    search_new_models
    search_new_engines
    search_techniques
    check_engine_updates
    generate_action_items

    # Append footer
    cat >> "${WEEKLY_REPORT}" << EOF

---
*Report generated by AgentHarness weekly_optimize.sh*
*Next scan: $(date -d '+7 days' '+%Y-%m-%d' 2>/dev/null || date -v+7d '+%Y-%m-%d' 2>/dev/null || echo "next week")*
EOF

    log_ok "Report saved to: ${WEEKLY_REPORT}"

    # Notify via Telegram
    notify "Weekly Optimization Report ready. See: ${WEEKLY_REPORT}"

    # Print summary to stdout
    echo ""
    cat "${WEEKLY_REPORT}"
}

main "$@"
