#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# build_inference.sh — Build both stock llama.cpp and ik_llama.cpp for comparison
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

LLAMA_DIR="/opt/llama.cpp"
IK_LLAMA_DIR="/opt/ik_llama"
BIN_DIR="/usr/local/bin"

# -----------------------------------------------------------------------------
# Detect hardware capabilities for optimal build flags
# -----------------------------------------------------------------------------
detect_cpu_features() {
    log_info "Detecting CPU features..."

    CPU_MODEL=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)
    CPU_CORES=$(nproc)
    HAS_AVX2=$(grep -q 'avx2' /proc/cpuinfo && echo "yes" || echo "no")
    HAS_AVX512=$(grep -q 'avx512' /proc/cpuinfo && echo "yes" || echo "no")
    HAS_FMA=$(grep -q 'fma' /proc/cpuinfo && echo "yes" || echo "no")
    TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)

    # Detect NUMA topology
    if command -v numactl &>/dev/null; then
        NUMA_NODES=$(numactl --hardware 2>/dev/null | grep -c "^node [0-9]" || echo "1")
    else
        NUMA_NODES="1"
    fi

    log_info "CPU: ${CPU_MODEL}"
    log_info "Cores: ${CPU_CORES}"
    log_info "RAM: ${TOTAL_RAM_GB}GB"
    log_info "AVX2: ${HAS_AVX2} | AVX-512: ${HAS_AVX512} | FMA: ${HAS_FMA}"
    log_info "NUMA nodes: ${NUMA_NODES}"

    # Store for other scripts
    mkdir -p /opt/agentharness
    cat > /opt/agentharness/hw_profile.env << EOF
CPU_MODEL="${CPU_MODEL}"
CPU_CORES=${CPU_CORES}
TOTAL_RAM_GB=${TOTAL_RAM_GB}
HAS_AVX2=${HAS_AVX2}
HAS_AVX512=${HAS_AVX512}
HAS_FMA=${HAS_FMA}
NUMA_NODES=${NUMA_NODES}
DETECTED_AT="$(date -Iseconds)"
EOF
    log_ok "Hardware profile saved to /opt/agentharness/hw_profile.env"
}

# -----------------------------------------------------------------------------
# Install build dependencies
# -----------------------------------------------------------------------------
install_build_deps() {
    log_info "Installing build dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        git build-essential cmake \
        libcurl4-openssl-dev pkg-config \
        numactl
    log_ok "Build dependencies installed"
}

# -----------------------------------------------------------------------------
# Build a llama.cpp variant
# -----------------------------------------------------------------------------
build_llama_variant() {
    local name="$1"
    local repo_url="$2"
    local install_dir="$3"
    local bin_prefix="$4"

    log_info "Building ${name}..."

    # Clone or update
    if [ -d "${install_dir}" ]; then
        log_info "${name} directory exists, pulling latest..."
        cd "${install_dir}"
        git pull --ff-only || {
            log_warn "Pull failed, doing fresh clone..."
            cd /opt
            sudo rm -rf "${install_dir}"
            sudo mkdir -p "${install_dir}" && sudo chown "$USER:$USER" "${install_dir}"
            git clone "${repo_url}" "${install_dir}"
        }
    else
        sudo mkdir -p "${install_dir}" && sudo chown "$USER:$USER" "${install_dir}"
        git clone "${repo_url}" "${install_dir}"
    fi

    cd "${install_dir}"

    # Record git version
    local git_hash
    git_hash=$(git rev-parse --short HEAD)
    local git_date
    git_date=$(git log -1 --format=%ci)

    # Clean previous build
    rm -rf build

    # Build with optimal flags
    local cmake_flags=(
        -DCMAKE_BUILD_TYPE=Release
        -DGGML_NATIVE=ON
        -DLLAMA_CURL=ON
    )

    if [ "${HAS_AVX2}" = "yes" ]; then
        cmake_flags+=(-DGGML_AVX2=ON)
    fi
    if [ "${HAS_AVX512}" = "yes" ]; then
        cmake_flags+=(-DGGML_AVX512=ON)
    fi

    cmake -B build "${cmake_flags[@]}"
    cmake --build build -j"${CPU_CORES}"

    # Install binaries with prefix
    sudo ln -sf "${install_dir}/build/bin/llama-server" "${BIN_DIR}/${bin_prefix}-server"
    sudo ln -sf "${install_dir}/build/bin/llama-bench" "${BIN_DIR}/${bin_prefix}-bench"
    sudo ln -sf "${install_dir}/build/bin/llama-cli" "${BIN_DIR}/${bin_prefix}-cli"

    # Verify
    if "${BIN_DIR}/${bin_prefix}-server" --version &>/dev/null || "${BIN_DIR}/${bin_prefix}-bench" --help &>/dev/null; then
        log_ok "${name} built successfully (${git_hash}, ${git_date})"
    else
        log_error "${name} build verification failed"
        return 1
    fi

    # Record build info
    cat >> /opt/agentharness/hw_profile.env << EOF
${bin_prefix^^}_GIT_HASH="${git_hash}"
${bin_prefix^^}_GIT_DATE="${git_date}"
${bin_prefix^^}_BUILD_DATE="$(date -Iseconds)"
EOF
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    log_header "Building Inference Engines"

    detect_cpu_features
    install_build_deps

    # Build stock llama.cpp
    build_llama_variant \
        "stock llama.cpp" \
        "https://github.com/ggml-org/llama.cpp.git" \
        "${LLAMA_DIR}" \
        "llama"

    # Build ik_llama.cpp (optimized fork)
    build_llama_variant \
        "ik_llama.cpp" \
        "https://github.com/ikawrakow/ik_llama.cpp.git" \
        "${IK_LLAMA_DIR}" \
        "ik-llama"

    log_header "Build Complete"
    log_info "Stock llama.cpp: llama-server, llama-bench, llama-cli"
    log_info "ik_llama.cpp:    ik-llama-server, ik-llama-bench, ik-llama-cli"
    log_info ""
    log_info "Run 'scripts/benchmark.sh' to compare performance"
}

main "$@"
