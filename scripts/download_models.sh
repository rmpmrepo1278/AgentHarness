#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# download_models.sh — Download, verify, and catalog recommended models
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

MODEL_DIR="${AH_MODEL_DIR:-/opt/models}"
CATALOG_FILE="${AH_DATA_DIR}/model_catalog.json"

# -----------------------------------------------------------------------------
# Model registry — edit this to add/remove models
# -----------------------------------------------------------------------------
# Format: name|type|active_params|total_size_gb|primary_repo|primary_file_pattern|fallback_repo|fallback_file_pattern
declare -a MODEL_REGISTRY=(
    "qwen3.5-9b|dense|9B|5.5|Qwen/Qwen3.5-9B-GGUF|*q4_k_m*|unsloth/Qwen3.5-9B-GGUF|*Q4_K_M*"
    "qwen3.5-35b-a3b|moe|3B-of-35B|19|mudler/Qwen3.5-35B-A3B-APEX-GGUF|*Balanced*|unsloth/Qwen3.5-35B-A3B-GGUF|*Q4_K_M*"
    "gemma4-26b-a4b|moe|3.8B-of-26B|17|google/gemma-4-26b-a4b-it-gguf|*q4_k_m*|bartowski/gemma-4-26b-a4b-it-GGUF|*Q4_K_M*"
    "qwen3.5-0.6b-draft|dense|0.6B|0.5|Qwen/Qwen3.5-0.6B-GGUF|*q8_0*|unsloth/Qwen3.5-0.6B-GGUF|*Q8_0*"
)

# -----------------------------------------------------------------------------
# Determine which models to download based on available RAM
# -----------------------------------------------------------------------------
select_models() {
    log_info "Selecting models based on available RAM..."

    if [ -f "${AH_DATA_DIR}/hw_profile.env" ]; then
        source "${AH_DATA_DIR}/hw_profile.env"
    else
        TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
    fi

    # Estimate RAM used by OS + Docker containers
    local used_ram
    used_ram=$(awk '/MemAvailable/ {printf "%.0f", ($2)/1024/1024}' /proc/meminfo)
    local docker_ram
    docker_ram=$(docker stats --no-stream --format "{{.MemUsage}}" 2>/dev/null | \
        awk -F'/' '{gsub(/[^0-9.]/, "", $1); sum += $1} END {printf "%.1f", sum/1024}' 2>/dev/null || echo "4")

    log_info "Total RAM: ${TOTAL_RAM_GB}GB"
    log_info "Docker containers using ~${docker_ram}GB"

    # Available for models = Total - OS(2GB) - Docker - KV cache reserve(2GB)
    local available
    available=$(echo "${TOTAL_RAM_GB} - 2 - ${docker_ram} - 2" | bc 2>/dev/null || echo "20")
    log_info "Available for models: ~${available}GB"

    # Always download: draft model + fast model
    MODELS_TO_DOWNLOAD=("qwen3.5-0.6b-draft" "qwen3.5-9b")

    # Add large models if they fit
    if (( $(echo "${available} > 18" | bc -l) )); then
        MODELS_TO_DOWNLOAD+=("qwen3.5-35b-a3b")
        log_info "35B-A3B model fits (needs ~19GB, have ~${available}GB)"
    else
        log_warn "Skipping 35B-A3B — needs ~19GB but only ~${available}GB available"
        log_info "Will download the smaller APEX-Small quant if available"
    fi

    if (( $(echo "${available} > 16" | bc -l) )); then
        MODELS_TO_DOWNLOAD+=("gemma4-26b-a4b")
        log_info "Gemma 4 model fits (needs ~17GB, have ~${available}GB)"
    else
        log_warn "Skipping Gemma 4 — needs ~17GB but only ~${available}GB available"
    fi

    log_ok "Will download: ${MODELS_TO_DOWNLOAD[*]}"
}

# -----------------------------------------------------------------------------
# Download a single model with fallback
# -----------------------------------------------------------------------------
download_model() {
    local entry="$1"
    IFS='|' read -r name type active_params total_size primary_repo primary_pattern fallback_repo fallback_pattern <<< "${entry}"

    local dest="${MODEL_DIR}/${name}"
    mkdir -p "${dest}"

    # Skip if already downloaded
    if find "${dest}" -name "*.gguf" -size +100M 2>/dev/null | grep -q .; then
        local existing
        existing=$(find "${dest}" -name "*.gguf" -printf '%f (%s bytes)\n' 2>/dev/null | head -1)
        log_info "${name}: Already downloaded (${existing}). Skipping."
        return 0
    fi

    log_info "Downloading ${name} from ${primary_repo}..."

    # Try primary source
    if huggingface-cli download "${primary_repo}" \
        --include "${primary_pattern}" \
        --local-dir "${dest}" 2>/dev/null; then
        log_ok "${name}: Downloaded from ${primary_repo}"
        return 0
    fi

    # Try fallback
    log_warn "${name}: Primary source failed, trying fallback ${fallback_repo}..."
    if huggingface-cli download "${fallback_repo}" \
        --include "${fallback_pattern}" \
        --local-dir "${dest}" 2>/dev/null; then
        log_ok "${name}: Downloaded from ${fallback_repo} (fallback)"
        return 0
    fi

    log_error "${name}: All download sources failed"
    return 1
}

# -----------------------------------------------------------------------------
# Build model catalog (JSON) for other scripts to consume
# -----------------------------------------------------------------------------
build_catalog() {
    log_info "Building model catalog..."

    local catalog="["
    local first=true

    for entry in "${MODEL_REGISTRY[@]}"; do
        IFS='|' read -r name type active_params total_size _ _ _ _ <<< "${entry}"
        local dest="${MODEL_DIR}/${name}"

        # Find the actual GGUF file
        local gguf_file
        gguf_file=$(find "${dest}" -name "*.gguf" -type f 2>/dev/null | head -1)

        if [ -n "${gguf_file}" ]; then
            local file_size_gb
            file_size_gb=$(stat --printf='%s' "${gguf_file}" 2>/dev/null | awk '{printf "%.1f", $1/1024/1024/1024}')

            if [ "${first}" = true ]; then
                first=false
            else
                catalog+=","
            fi

            catalog+=$(cat <<ENTRY

  {
    "name": "${name}",
    "type": "${type}",
    "active_params": "${active_params}",
    "expected_size_gb": "${total_size}",
    "actual_size_gb": "${file_size_gb}",
    "gguf_path": "${gguf_file}",
    "downloaded_at": "$(date -Iseconds)"
  }
ENTRY
)
        fi
    done

    catalog+=$'\n]'
    echo "${catalog}" > "${CATALOG_FILE}"
    log_ok "Model catalog written to ${CATALOG_FILE}"
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
print_summary() {
    log_header "Downloaded Models"
    echo ""
    printf "%-25s %-8s %-12s %-10s %s\n" "MODEL" "TYPE" "ACTIVE" "SIZE" "PATH"
    printf "%-25s %-8s %-12s %-10s %s\n" "-----" "----" "------" "----" "----"

    for entry in "${MODEL_REGISTRY[@]}"; do
        IFS='|' read -r name type active_params _ _ _ _ _ <<< "${entry}"
        local dest="${MODEL_DIR}/${name}"
        local gguf_file
        gguf_file=$(find "${dest}" -name "*.gguf" -type f 2>/dev/null | head -1)

        if [ -n "${gguf_file}" ]; then
            local file_size
            file_size=$(du -h "${gguf_file}" 2>/dev/null | cut -f1)
            printf "%-25s %-8s %-12s %-10s %s\n" "${name}" "${type}" "${active_params}" "${file_size}" "${gguf_file}"
        fi
    done
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    log_header "Downloading Models"

    # Ensure huggingface-cli is available
    if ! command -v huggingface-cli &>/dev/null; then
        log_info "Installing huggingface_hub..."
        pip install --quiet --upgrade huggingface_hub
    fi

    mkdir -p "${MODEL_DIR}"
    ensure_dir "${AH_DATA_DIR}"

    select_models

    local failed=0
    for entry in "${MODEL_REGISTRY[@]}"; do
        IFS='|' read -r name _ _ _ _ _ _ _ <<< "${entry}"
        # Check if this model is in our download list
        for selected in "${MODELS_TO_DOWNLOAD[@]}"; do
            if [ "${name}" = "${selected}" ]; then
                download_model "${entry}" || ((failed++))
                break
            fi
        done
    done

    build_catalog
    print_summary

    if [ "${failed}" -gt 0 ]; then
        log_warn "${failed} model(s) failed to download. Check network and retry."
        return 1
    fi

    log_ok "All models downloaded successfully"
}

main "$@"
