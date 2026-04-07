#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# install.sh — AgentHarness v2 Installer
#
# Thin bash wrapper that delegates to the Python discovery engine where
# possible. Only does bash-specific things: apt install, systemd setup,
# cron, engine builds, model downloads.
#
# Usage: ./install.sh [--dry-run] [--phase=N] [--skip-models] [--skip-benchmark]
#                      [--minimal] [--doctor] [--help]
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/common.sh"

export AGENTHARNESS_HOME="${AGENTHARNESS_HOME:-$SCRIPT_DIR}"
export AH_DATA_DIR="${AH_DATA_DIR:-$AGENTHARNESS_HOME/data}"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
SKIP_MODELS=false
SKIP_BENCHMARK=false
SKIP_ENGINES=false
MINIMAL=false
DRY_RUN=false
RUN_PHASE=""

for arg in "$@"; do
    case "$arg" in
        --skip-models)    SKIP_MODELS=true ;;
        --skip-benchmark) SKIP_BENCHMARK=true ;;
        --skip-engines)   SKIP_ENGINES=true ;;
        --minimal)        MINIMAL=true; SKIP_BENCHMARK=true ;;
        --dry-run)        DRY_RUN=true ;;
        --phase=*)        RUN_PHASE="${arg#--phase=}" ;;
        --doctor)         exec python3 "${AGENTHARNESS_HOME}/cli.py" selftest ;;
        --doctor-fix)     exec python3 "${AGENTHARNESS_HOME}/cli.py" selftest --fix 2>/dev/null \
                              || exec python3 "${AGENTHARNESS_HOME}/cli.py" selftest ;;
        --help)
            cat << 'HELP'
Usage: ./install.sh [OPTIONS]

Options:
  --dry-run          Check everything without making changes
  --phase=N          Run only phase N (0-9)
  --doctor           Run diagnostics (python3 cli.py selftest)
  --skip-models      Skip model downloads
  --skip-engines     Skip inference engine builds
  --skip-benchmark   Skip benchmarking
  --minimal          Minimal install (engines + fast model only)

Phases:
  0  Python discovery (hardware, services, agents, paths)
  1  Install dependencies (apt + pip)
  2  Build inference engines
  3  Download models
  4  Set up SearXNG
  5  Set up systemd services (or cron fallback)
  6  Benchmark and auto-select
  7  Configuration (env, API keys)
  8  Log rotation
  9  Validation (python3 cli.py selftest)

Examples:
  ./install.sh --dry-run           # See what would happen
  ./install.sh --phase=0           # Just run discovery
  ./install.sh --phase=2           # Just build engines
  ./install.sh --doctor            # Diagnose problems
HELP
            exit 0 ;;
    esac
done

# =============================================================================
# PHASE 0: Python Discovery (replaces ~600 lines of bash discovery)
# =============================================================================
phase_0() {
    log_header "Phase 0: Discovery"
    log_info "Running Python discovery engine..."
    python3 "${AGENTHARNESS_HOME}/cli.py" discover
    log_ok "Discovery complete — results in ${AH_DATA_DIR}/state.json"
}

# =============================================================================
# PHASE 1: Install system dependencies
# =============================================================================
phase_1() {
    log_header "Phase 1: Installing Dependencies"

    # Check Python 3.9+
    python3 -c "import sys; assert sys.version_info >= (3,9), 'Python 3.9+ required'" || {
        log_error "Python 3.9+ required"
        exit 1
    }
    log_ok "Python $(python3 --version 2>&1 | awk '{print $2}')"

    # System packages (apt — only on Debian/Ubuntu)
    if command -v apt-get &>/dev/null; then
        log_info "Installing system packages via apt..."
        sudo apt-get update -qq
        local packages=(
            git build-essential cmake
            libcurl4-openssl-dev pkg-config
            numactl
            python3 python3-pip python3-venv
            curl wget jq bc
            sqlite3
        )
        sudo apt-get install -y -qq "${packages[@]}"
    else
        log_info "Not a Debian/Ubuntu system — skipping apt packages"
        log_info "Ensure you have: git, cmake, curl, jq, sqlite3 installed"
    fi

    # Python packages
    if [ -f "${AGENTHARNESS_HOME}/requirements.txt" ]; then
        log_info "Installing Python dependencies..."
        pip3 install --user -r "${AGENTHARNESS_HOME}/requirements.txt" 2>/dev/null \
            || pip3 install --user -r "${AGENTHARNESS_HOME}/requirements.txt"
    fi

    log_ok "Dependencies installed"
}

# =============================================================================
# PHASE 2: Build inference engines
# =============================================================================
phase_2() {
    log_header "Phase 2: Building Inference Engines"

    local build_script="${AGENTHARNESS_HOME}/scripts/build_inference.sh"
    if [ -f "${build_script}" ]; then
        bash "${build_script}"
    else
        log_warn "Build script not found: ${build_script}"
        log_info "Skipping engine build"
    fi
}

# =============================================================================
# PHASE 3: Download models
# =============================================================================
phase_3() {
    log_header "Phase 3: Downloading Models"

    local dl_script="${AGENTHARNESS_HOME}/scripts/download_models.sh"
    if [ -f "${dl_script}" ]; then
        bash "${dl_script}"
    else
        log_warn "Download script not found: ${dl_script}"
    fi
}

# =============================================================================
# PHASE 4: Set up SearXNG
# =============================================================================
phase_4() {
    log_header "Phase 4: Setting Up SearXNG"

    if ! command -v docker &>/dev/null; then
        log_info "Docker not found — skipping SearXNG"
        return
    fi

    # Already running?
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q searxng; then
        log_ok "SearXNG is already running"
        return
    fi

    local compose_dir="${AGENTHARNESS_HOME}/config/searxng"
    if [ -f "${compose_dir}/docker-compose.yml" ]; then
        log_info "Starting SearXNG via docker compose..."
        cd "${compose_dir}" && docker compose up -d 2>/dev/null || {
            log_warn "docker compose failed — SearXNG not started"
            return
        }
        sleep 5
        if curl -sf "http://localhost:8888/search?q=test&format=json" &>/dev/null; then
            log_ok "SearXNG is running on port 8888"
        else
            log_warn "SearXNG started but search test failed. Check: docker logs searxng"
        fi
    else
        log_info "No SearXNG compose file found — skipping"
    fi
}

# =============================================================================
# PHASE 5: Set up systemd services (with cron fallback)
# =============================================================================
phase_5() {
    log_header "Phase 5: Setting Up Services"

    if command -v systemctl &>/dev/null; then
        log_info "Setting up systemd services..."

        for svc in agentharness-scheduler agentharness-watchdog llama-primary llama-fast; do
            local src="${AGENTHARNESS_HOME}/config/systemd/${svc}.service"
            if [ -f "${src}" ]; then
                sudo cp "${src}" /etc/systemd/system/
                log_ok "Installed: ${svc}"
            fi
        done

        # Timer
        local timer="${AGENTHARNESS_HOME}/config/systemd/agentharness-watchdog.timer"
        [ -f "${timer}" ] && sudo cp "${timer}" /etc/systemd/system/

        sudo systemctl daemon-reload

        # Enable scheduler + watchdog
        sudo systemctl enable agentharness-scheduler agentharness-watchdog.timer 2>/dev/null || true
        log_ok "Systemd services enabled"
    else
        log_info "systemd not found — setting up cron fallback"

        local scheduler_cmd="cd ${AGENTHARNESS_HOME} && python3 -m core.scheduler.scheduler --data-dir ${AH_DATA_DIR}"
        local cron_line="*/15 * * * * ${scheduler_cmd} >> ${AH_DATA_DIR}/logs/scheduler.log 2>&1"

        # Add scheduler cron, removing any old entry first
        (crontab -l 2>/dev/null | grep -v "core.scheduler.scheduler\|scheduler.sh"; echo "${cron_line}") | crontab -
        log_ok "Cron scheduler installed (every 15 minutes)"
    fi
}

# =============================================================================
# PHASE 6: Benchmark
# =============================================================================
phase_6() {
    log_header "Phase 6: Benchmarking"

    local bench_script="${AGENTHARNESS_HOME}/scripts/benchmark.sh"
    if [ -f "${bench_script}" ]; then
        bash "${bench_script}"
    else
        log_warn "Benchmark script not found: ${bench_script}"
    fi
}

# =============================================================================
# PHASE 7: Configuration
# =============================================================================
phase_7() {
    log_header "Phase 7: Configuration"

    # Link master .env if discovered
    if [ -f "${AH_DATA_DIR}/state.json" ]; then
        local master_env
        master_env=$(python3 -c "
import json, pathlib
state = json.loads(pathlib.Path('${AH_DATA_DIR}/state.json').read_text())
print(state.get('master_env', ''))
" 2>/dev/null || echo "")
        if [ -n "${master_env}" ] && [ -f "${master_env}" ]; then
            if [ ! -f "${AH_DATA_DIR}/.env" ]; then
                ln -sf "${master_env}" "${AH_DATA_DIR}/.env"
                log_ok "Linked to master .env: ${master_env}"
            else
                log_info ".env already exists at ${AH_DATA_DIR}/.env"
            fi
        fi
    fi

    # Suggest free tier providers
    echo ""
    log_info "Free LLM providers (set env vars to enable):"
    echo "    GROQ_API_KEY     — Groq (200 req/day)     https://console.groq.com"
    echo "    GOOGLE_API_KEY   — Gemini (1500 req/day)   https://aistudio.google.com/apikey"
    echo "    CEREBRAS_API_KEY — Cerebras (1000 req/day) https://cloud.cerebras.ai"
}

# =============================================================================
# PHASE 8: Log rotation
# =============================================================================
phase_8() {
    log_header "Phase 8: Setting Up Log Rotation"

    local src="${AGENTHARNESS_HOME}/config/logrotate/agentharness"
    if [ -f "${src}" ] && command -v logrotate &>/dev/null; then
        sudo cp "${src}" /etc/logrotate.d/agentharness
        log_ok "Logrotate installed"
    else
        log_info "Logrotate not available or config not found — skipping"
    fi
}

# =============================================================================
# PHASE 9: Validation (delegates to Python selftest)
# =============================================================================
phase_9() {
    log_header "Phase 9: Validation"
    python3 "${AGENTHARNESS_HOME}/cli.py" selftest
    python3 "${AGENTHARNESS_HOME}/cli.py" bundle list 2>/dev/null || true
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo ""
    echo "  AgentHarness v2 Installer"
    echo "  ========================="
    echo "  Install dir: ${AGENTHARNESS_HOME}"
    echo "  Data dir:    ${AH_DATA_DIR}"
    echo ""

    # Create data directories
    mkdir -p "${AH_DATA_DIR}"/{logs,reports,proposals,briefings,custom}

    # Single-phase mode
    if [ -n "${RUN_PHASE}" ]; then
        "phase_${RUN_PHASE}"
        return
    fi

    # Dry-run mode
    if [ "${DRY_RUN}" = true ]; then
        log_info "DRY RUN — would execute phases 0-9"
        python3 "${AGENTHARNESS_HOME}/cli.py" validate 2>/dev/null \
            || python3 "${AGENTHARNESS_HOME}/cli.py" selftest
        return
    fi

    # Full install
    phase_0

    phase_1

    if [ "${SKIP_ENGINES}" != true ]; then
        phase_2
    else
        log_info "Skipping Phase 2 (engines — --skip-engines)"
    fi

    if [ "${SKIP_MODELS}" != true ]; then
        phase_3
    else
        log_info "Skipping Phase 3 (models — --skip-models)"
    fi

    phase_4
    phase_5

    if [ "${SKIP_BENCHMARK}" != true ]; then
        phase_6
    else
        log_info "Skipping Phase 6 (benchmark — --skip-benchmark)"
    fi

    phase_7
    phase_8
    phase_9

    echo ""
    log_ok "Installation complete."
    echo ""
    echo "  Status:    python3 cli.py status"
    echo "  Smoketest: python3 cli.py smoketest"
    echo "  Dashboard: uvicorn core.observe.dashboard:create_app --factory --port 9100"
    echo ""
}

main
