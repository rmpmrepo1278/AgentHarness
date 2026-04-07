#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# install.sh — AgentHarness Self-Bootstrapping Installer
#
# Usage: ./install.sh [--skip-models] [--skip-benchmark] [--minimal]
#
# Detects your hardware, installs dependencies, builds inference engines,
# downloads models, benchmarks everything, picks the best config, sets up
# monitoring cron jobs, and validates the installation.
#
# Run this once on your homelab. It will detect existing tools and skip
# what's already set up.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/common.sh"

# Parse arguments
SKIP_MODELS=false
SKIP_BENCHMARK=false
MINIMAL=false
DRY_RUN=false
RUN_PHASE=""
for arg in "$@"; do
    case "$arg" in
        --skip-models)    SKIP_MODELS=true ;;
        --skip-benchmark) SKIP_BENCHMARK=true ;;
        --minimal)        MINIMAL=true; SKIP_BENCHMARK=true ;;
        --dry-run)        DRY_RUN=true ;;
        --phase=*)        RUN_PHASE="${arg#--phase=}" ;;
        --doctor)         exec bash "${SCRIPT_DIR}/scripts/doctor.sh" "${@:2}"; exit ;;
        --doctor-fix)     exec bash "${SCRIPT_DIR}/scripts/doctor.sh" --fix; exit ;;
        --help)
            cat << 'HELP'
Usage: ./install.sh [OPTIONS]

Options:
  --dry-run          Check everything without making changes
  --phase=N          Run only phase N (0-10)
  --doctor           Run the diagnostic doctor
  --doctor-fix       Run doctor with auto-fix enabled
  --skip-models      Skip model downloads
  --skip-benchmark   Skip benchmarking
  --minimal          Minimal install (engines + fast model only)

Phases:
  0  Deep discovery (scan existing automations + configs)
  1  Install dependencies
  2  Build inference engines
  3  Download models
  4  Set up SearXNG
  5  Set up systemd services
  6  Benchmark and auto-select
  7  Config discovery (env file)
  8  Smart scheduler
  8.5 Plugin registry
  9  Convenience aliases
  10 Validation

Examples:
  ./install.sh --dry-run           # See what would happen
  ./install.sh --phase=0           # Just run discovery
  ./install.sh --phase=2           # Just build engines
  ./install.sh --doctor            # Diagnose problems
  ./install.sh --doctor-fix        # Diagnose and auto-fix

If something breaks:
  1. Run: ./install.sh --doctor
  2. It will tell you exactly what's wrong and how to fix it
  3. Or: ./install.sh --doctor-fix  (auto-fixes safe issues)
  4. Or: ./install.sh --phase=N    (re-run the specific phase that failed)
HELP
            exit 0 ;;
    esac
done

# =============================================================================
# PHASE 0: Detect existing environment
# =============================================================================
detect_existing() {
    log_header "Phase 0: Deep Discovery"
    log_info "Scanning everything already on this machine..."
    log_info "Scripts, cron jobs, systemd services, Docker configs, n8n workflows,"
    log_info "env files, API keys, running services — all of it."
    echo ""

    # --- 0a: Discover all automations ---
    bash "${SCRIPT_DIR}/scripts/discover_automations.sh"

    # --- 0b: Discover all configs/secrets ---
    bash "${SCRIPT_DIR}/scripts/discover_config.sh"

    # --- 0c: Build EXISTING and BENCHMARK_TOOLS arrays from catalog ---
    EXISTING=()
    BENCHMARK_TOOLS=()

    if [ -f /opt/agentharness/automation_catalog.json ]; then
        # Extract benchmark tools and existing capabilities from catalog
        while IFS='|' read -r entry_type entry_value; do
            case "${entry_type}" in
                EXISTING) EXISTING+=("${entry_value}") ;;
                BENCH)    BENCHMARK_TOOLS+=("${entry_value}") ;;
            esac
        done < <(python3 -c "
import json

catalog = json.load(open('/opt/agentharness/automation_catalog.json'))
items = catalog['items']

for item in items:
    t = item.get('type', '')
    path = item.get('path', '')
    name = item.get('name', '')
    caps = item.get('capabilities', [])

    if t == 'systemd_service' and 'llama' in name.lower():
        print(f'EXISTING|systemd:{name}')
    if t in ('shell_script', 'python_script'):
        if 'benchmark' in caps or 'bench' in path.lower():
            print(f'BENCH|{path}')
        for cap in ('monitoring', 'self-healing', 'cleanup', 'llm', 'deployment', 'backup'):
            if cap in caps:
                print(f'EXISTING|has_{cap}:{path}')
                break
    if t == 'n8n_workflow':
        print(f'EXISTING|n8n:{name}')
    if t == 'docker_compose':
        for svc in item.get('services', []):
            print(f'EXISTING|compose_svc:{svc}')
" 2>/dev/null)
    fi

    # Also check directly for key components
    command -v ik-llama-server &>/dev/null && EXISTING+=("ik-llama-server")
    command -v llama-server &>/dev/null && EXISTING+=("llama-server")
    command -v aider &>/dev/null && EXISTING+=("aider")
    command -v opencode &>/dev/null && EXISTING+=("opencode")
    python3 -c "import smolagents" 2>/dev/null && EXISTING+=("smolagents")
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q searxng && EXISTING+=("searxng")

    local model_count
    model_count=$(find /opt/models -name "*.gguf" -type f 2>/dev/null | wc -l || echo "0")
    [ "${model_count}" -gt 0 ] && EXISTING+=("models:${model_count}")
    [ -f /opt/agentharness/benchmark_results.json ] && EXISTING+=("benchmark_results")

    echo ""
    log_ok "Discovered ${#EXISTING[@]} existing component(s) and ${#BENCHMARK_TOOLS[@]} benchmark tool(s)"
    log_info "Catalog: /opt/agentharness/automation_catalog.json"
    log_info "Config:  /opt/agentharness/.env"
    echo ""
    log_info "AgentHarness will AUGMENT existing automations, not replace them."
    echo ""
}

# =============================================================================
# PHASE 1: Install system dependencies
# =============================================================================
install_dependencies() {
    log_header "Phase 1: Installing Dependencies"

    sudo apt-get update -qq

    # Core build tools
    local packages=(
        git build-essential cmake
        libcurl4-openssl-dev pkg-config
        numactl
        python3 python3-pip python3-venv
        curl wget jq bc
        sqlite3
    )

    sudo apt-get install -y -qq "${packages[@]}"

    # Python packages
    pip install --quiet --upgrade \
        huggingface_hub \
        smolagents \
        aider-chat 2>/dev/null || pip install --quiet --upgrade huggingface_hub smolagents

    log_ok "Dependencies installed"
}

# =============================================================================
# PHASE 2: Build inference engines
# =============================================================================
build_engines() {
    log_header "Phase 2: Building Inference Engines"

    # Skip if both already exist and are recent
    local skip_build=false
    if [[ " ${EXISTING[*]} " =~ " ik-llama-server " ]] && [[ " ${EXISTING[*]} " =~ " llama-server " ]]; then
        log_info "Both engines already installed."
        read -rp "Rebuild from latest source? [y/N] " rebuild
        if [ "${rebuild,,}" != "y" ]; then
            skip_build=true
        fi
    fi

    if [ "${skip_build}" = false ]; then
        bash "${SCRIPT_DIR}/scripts/build_inference.sh"
    else
        log_info "Skipping engine build (already installed)"
        # Still need hardware profile
        if [ ! -f /opt/agentharness/hw_profile.env ]; then
            source "${SCRIPT_DIR}/scripts/common.sh"
            # Minimal hardware detection
            mkdir -p /opt/agentharness
            cat > /opt/agentharness/hw_profile.env << EOF
CPU_MODEL="$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
CPU_CORES=$(nproc)
TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
HAS_AVX2=$(grep -q 'avx2' /proc/cpuinfo && echo "yes" || echo "no")
HAS_AVX512=$(grep -q 'avx512' /proc/cpuinfo && echo "yes" || echo "no")
NUMA_NODES=$(numactl --hardware 2>/dev/null | grep -c "^node [0-9]" || echo "1")
DETECTED_AT="$(date -Iseconds)"
EOF
        fi
    fi
}

# =============================================================================
# PHASE 3: Download models
# =============================================================================
download_models() {
    log_header "Phase 3: Downloading Models"

    if [ "${SKIP_MODELS}" = true ]; then
        log_info "Skipping model downloads (--skip-models)"
        # Build catalog from existing models
        if [ -d /opt/models ] && find /opt/models -name "*.gguf" -type f 2>/dev/null | grep -q .; then
            log_info "Building catalog from existing models..."
            bash "${SCRIPT_DIR}/scripts/download_models.sh" 2>/dev/null || true
        fi
        return
    fi

    bash "${SCRIPT_DIR}/scripts/download_models.sh"
}

# =============================================================================
# PHASE 4: Set up SearXNG
# =============================================================================
setup_searxng() {
    log_header "Phase 4: Setting Up SearXNG"

    if [[ " ${EXISTING[*]} " =~ " searxng " ]]; then
        log_info "SearXNG is already running. Skipping."
        return
    fi

    ensure_dir /opt/searxng

    # Copy config files
    cp "${SCRIPT_DIR}/config/searxng/docker-compose.yml" /opt/searxng/
    cp "${SCRIPT_DIR}/config/searxng/settings.yml" /opt/searxng/

    # Generate secret key
    local secret
    secret=$(openssl rand -hex 32)
    sed -i "s/__SEARXNG_SECRET__/${secret}/" /opt/searxng/settings.yml

    # Check if homelab network exists, create if not
    if ! docker network inspect homelab &>/dev/null; then
        log_info "Creating 'homelab' Docker network..."
        docker network create homelab || {
            # Fallback: use bridge network instead
            sed -i 's/external: true/external: false/' /opt/searxng/docker-compose.yml
            sed -i '/name: homelab/d' /opt/searxng/docker-compose.yml
        }
    fi

    cd /opt/searxng
    docker compose up -d

    # Wait and verify
    sleep 5
    if curl -sf "http://localhost:8888/search?q=test&format=json" &>/dev/null; then
        log_ok "SearXNG is running on port 8888"
    else
        log_warn "SearXNG started but search test failed. Check: docker logs searxng"
    fi
}

# =============================================================================
# PHASE 5: Set up systemd services
# =============================================================================
setup_services() {
    log_header "Phase 5: Setting Up Systemd Services"

    source /opt/agentharness/hw_profile.env

    # Determine model paths from catalog
    local primary_model fast_model
    if [ -f /opt/agentharness/model_catalog.json ]; then
        primary_model=$(python3 -c "
import json
catalog = json.load(open('/opt/agentharness/model_catalog.json'))
# Prefer MoE models for primary
for m in catalog:
    if m['type'] == 'moe' and 'draft' not in m['name']:
        print(m['gguf_path']); break
else:
    # Fallback to largest dense
    dense = [m for m in catalog if m['type'] == 'dense' and 'draft' not in m['name']]
    if dense:
        print(dense[0]['gguf_path'])
" 2>/dev/null || echo "")

        fast_model=$(python3 -c "
import json
catalog = json.load(open('/opt/agentharness/model_catalog.json'))
# Find smallest non-draft dense model
dense = [m for m in catalog if m['type'] == 'dense' and 'draft' not in m['name']]
dense.sort(key=lambda x: float(x.get('actual_size_gb', 999)))
if dense:
    print(dense[0]['gguf_path'])
" 2>/dev/null || echo "")
    fi

    local threads="${CPU_CORES}"

    # Install primary service
    if [ -n "${primary_model}" ]; then
        sudo cp "${SCRIPT_DIR}/config/systemd/llama-primary.service" /etc/systemd/system/
        sudo sed -i "s|__MODEL_PATH__|${primary_model}|g" /etc/systemd/system/llama-primary.service
        sudo sed -i "s|__THREADS__|${threads}|g" /etc/systemd/system/llama-primary.service
        sudo systemctl daemon-reload
        sudo systemctl enable llama-primary
        log_ok "Primary service configured: ${primary_model}"
    else
        log_warn "No primary model found. Skipping primary service setup."
    fi

    # Install fast service
    if [ -n "${fast_model}" ]; then
        sudo cp "${SCRIPT_DIR}/config/systemd/llama-fast.service" /etc/systemd/system/
        sudo sed -i "s|__MODEL_PATH__|${fast_model}|g" /etc/systemd/system/llama-fast.service
        sudo sed -i "s|__THREADS__|${threads}|g" /etc/systemd/system/llama-fast.service
        sudo systemctl daemon-reload
        sudo systemctl enable llama-fast
        log_ok "Fast service configured: ${fast_model}"
    fi

    # Start primary (not both — RAM constraint)
    if [ -n "${primary_model}" ]; then
        log_info "Starting primary LLM server..."
        sudo systemctl start llama-primary
        sleep 15

        if curl -sf http://localhost:8080/health &>/dev/null; then
            log_ok "Primary LLM server is healthy on port 8080"
        else
            log_warn "Primary server may still be loading. Check: sudo journalctl -u llama-primary -f"
        fi
    fi
}

# =============================================================================
# PHASE 6: Benchmark and auto-select best config
# =============================================================================
run_benchmarks() {
    log_header "Phase 6: Benchmarking"

    if [ "${SKIP_BENCHMARK}" = true ]; then
        log_info "Skipping benchmarks (--skip-benchmark)"
        return
    fi

    # Check for existing benchmark tools and integrate them
    if [ ${#BENCHMARK_TOOLS[@]} -gt 0 ]; then
        log_info "Found ${#BENCHMARK_TOOLS[@]} existing benchmark tool(s):"
        for tool in "${BENCHMARK_TOOLS[@]}"; do
            log_info "  - ${tool}"
        done
        echo ""
        log_info "Running existing benchmark tools first..."
        for tool in "${BENCHMARK_TOOLS[@]}"; do
            if [[ "${tool}" == *.py ]]; then
                log_info "Running: python3 ${tool}"
                python3 "${tool}" 2>&1 | tee "/opt/agentharness/reports/existing_bench_$(basename "${tool}" .py)_$(timestamp).txt" || true
            elif [[ "${tool}" == *.sh ]]; then
                log_info "Running: bash ${tool}"
                bash "${tool}" 2>&1 | tee "/opt/agentharness/reports/existing_bench_$(basename "${tool}" .sh)_$(timestamp).txt" || true
            fi
        done
        echo ""
    fi

    # Run our benchmark suite
    bash "${SCRIPT_DIR}/scripts/benchmark.sh"
}

# =============================================================================
# PHASE 7: Discover existing configs and generate .env
# =============================================================================
setup_env() {
    log_header "Phase 7: Config Discovery"

    log_info "Scanning system for existing API keys, service URLs, and configs..."
    log_info "This avoids re-entering values already configured on this machine."
    echo ""

    bash "${SCRIPT_DIR}/scripts/discover_config.sh"

    echo ""
    log_info "Review and verify: nano /opt/agentharness/.env"
}

# =============================================================================
# PHASE 8: Set up smart scheduler (network-aware, replaces fixed cron jobs)
# =============================================================================
setup_scheduler() {
    log_header "Phase 8: Setting Up Smart Scheduler"

    ensure_dir /opt/agentharness/logs

    # Install scheduler as a cron that runs every 15 minutes
    # The scheduler itself decides what to run based on network state + time
    local existing_cron
    existing_cron=$(crontab -l 2>/dev/null || echo "")

    local new_cron="${existing_cron}"

    # Remove old fixed cron jobs if they exist (replaced by scheduler)
    new_cron=$(echo "${new_cron}" | grep -v "daily_improve\|weekly_optimize\|benchmark.sh\|AgentHarness:" || true)

    # Add the smart scheduler (every 15 minutes)
    if ! echo "${new_cron}" | grep -q "scheduler.sh"; then
        new_cron+=$'\n'"# AgentHarness: Smart scheduler (network-aware, runs every 15 min)"
        new_cron+=$'\n'"*/15 * * * * /bin/bash ${SCRIPT_DIR}/scripts/scheduler.sh >> /opt/agentharness/logs/scheduler.log 2>&1"
        log_ok "Added smart scheduler cron (every 15 minutes)"
    else
        log_info "Smart scheduler cron already exists"
    fi

    echo "${new_cron}" | crontab -

    log_info "Schedule logic:"
    echo "  OFFLINE (11 PM - 7:15 AM PT): benchmarks, cleanup, log analysis, self-improvement"
    echo "  ONLINE  (7:15 AM - 11 PM PT): model downloads, web searches, git pulls, GitHub deploys"
    echo "  LAN-ONLY (offline + ethernet): above + cross-machine tasks with mini PC"
    echo ""
    log_info "Override schedule in /opt/agentharness/.env:"
    echo "  OFFLINE_START_HOUR=23"
    echo "  ONLINE_START_HOUR=7"
    echo "  MINIPC_IP=192.168.x.x  (set when mini PC arrives)"

    log_ok "Smart scheduler configured"
}

# =============================================================================
# PHASE 8.5: Install plugin registry and custom scripts directory
# =============================================================================
setup_registry() {
    log_header "Phase 8.5: Plugin Registry"

    # Copy registry to /opt/agentharness/config/
    ensure_dir /opt/agentharness/config
    if [ ! -f /opt/agentharness/config/harness_registry.yaml ]; then
        cp "${SCRIPT_DIR}/config/harness_registry.yaml" /opt/agentharness/config/
        log_ok "Registry installed: /opt/agentharness/config/harness_registry.yaml"
    else
        log_info "Registry already exists. Preserving existing config."
    fi

    # Create custom scripts directory
    ensure_dir /opt/agentharness/custom
    log_info "Drop custom scripts in: /opt/agentharness/custom/"

    # Copy bundled skills to discovered OpenClaw skills directory
    if [ -f /opt/agentharness/openclaw_paths.env ]; then
        source /opt/agentharness/openclaw_paths.env
        if [ -n "${OPENCLAW_SKILLS_DIR:-}" ]; then
            for skill_dir in "${SCRIPT_DIR}/config/skills"/*/; do
                local skill_name
                skill_name=$(basename "${skill_dir}")
                local target="${OPENCLAW_SKILLS_DIR}/${skill_name}"
                if [ ! -d "${target}" ]; then
                    cp -r "${skill_dir}" "${target}"
                    log_ok "Installed skill: ${skill_name}"
                else
                    log_info "Skill ${skill_name} already exists — skipping"
                fi
            done
        fi
    fi

    # Install PyYAML if needed
    python3 -c "import yaml" 2>/dev/null || pip install --quiet pyyaml

    # --- Install ClawHub community skills ---
    log_info "Installing recommended ClawHub skills..."

    # Install clawhub CLI if not present
    if ! command -v clawhub &>/dev/null; then
        if command -v npm &>/dev/null; then
            npm install -g clawhub 2>/dev/null && log_ok "Installed clawhub CLI" || \
                log_warn "Failed to install clawhub CLI (npm). Install manually: npm i -g clawhub"
        else
            log_warn "npm not available — skipping clawhub CLI install"
        fi
    fi

    if command -v clawhub &>/dev/null; then
        local clawhub_skills=(
            "capability-evolver"    # Self-improving agent — analyzes failures and writes fixes
            "tavily"               # AI-optimized web search
            "memory-context"       # Enhanced long-term memory across sessions
        )

        for skill in "${clawhub_skills[@]}"; do
            if clawhub list 2>/dev/null | grep -q "${skill}"; then
                log_info "ClawHub skill already installed: ${skill}"
            else
                log_info "Installing ClawHub skill: ${skill}..."
                clawhub install "${skill}" 2>/dev/null && \
                    log_ok "Installed: ${skill}" || \
                    log_warn "Failed to install: ${skill} (install manually: clawhub install ${skill})"
            fi
        done
    else
        log_info "clawhub CLI not available. Install these skills manually:"
        echo "  npm i -g clawhub"
        echo "  clawhub install capability-evolver"
        echo "  clawhub install tavily"
        echo "  clawhub install memory-context"
    fi

    # --- Enable bundled OpenClaw skills ---
    if command -v openclaw &>/dev/null; then
        log_info "Enabling bundled OpenClaw skills..."
        local bundled_skills=(
            "weather"          # Weather forecasts for morning briefing
            "taskflow"         # Task management with priorities and deadlines
            "himalaya"         # Email client — deeper Gmail capabilities
            "github"           # GitHub integration — issues, repos
            "coding-agent"     # Code generation and editing
            "tmux"             # Terminal session management
        )

        for skill in "${bundled_skills[@]}"; do
            openclaw skills enable "${skill}" 2>/dev/null && \
                log_ok "Enabled bundled skill: ${skill}" || \
                log_info "Skill ${skill}: already enabled or not available"
        done
    else
        log_info "openclaw CLI not in PATH — bundled skills will be enabled when OpenClaw is configured"
    fi

    log_ok "Plugin registry ready"
    echo "  Add checks:    python3 scripts/registry_engine.py add_check ..."
    echo "  Add harnesses: python3 scripts/registry_engine.py add_harness ..."
    echo "  Or ask Chaguli via Telegram!"
}

# =============================================================================
# PHASE 9: Create convenience aliases
# =============================================================================
setup_aliases() {
    log_header "Phase 9: Setting Up Convenience Aliases"

    local alias_file="/opt/agentharness/aliases.sh"
    cat > "${alias_file}" << 'ALIASES'
# === AgentHarness Aliases ===
# Source this from your .bashrc: source /opt/agentharness/aliases.sh

# LLM management
alias llm-status='echo "=== LLM Servers ==="; curl -s http://localhost:8080/health 2>/dev/null && echo " Primary:8080 UP" || echo " Primary:8080 DOWN"; curl -s http://localhost:8081/health 2>/dev/null && echo " Fast:8081 UP" || echo " Fast:8081 DOWN"'
alias llm-primary='sudo systemctl stop llama-fast 2>/dev/null; sudo systemctl start llama-primary && echo "Switched to primary model"'
alias llm-fast='sudo systemctl stop llama-primary 2>/dev/null; sudo systemctl start llama-fast && echo "Switched to fast model"'
alias llm-logs='sudo journalctl -u llama-primary -f'
alias llm-metrics='curl -s http://localhost:8080/metrics 2>/dev/null || echo "Server not running"'

# Coding assistants (pointed at local LLM)
alias aide='aider --model openai/local --openai-api-base http://localhost:8080/v1 --openai-api-key not-needed'
alias aide-fast='aider --model openai/local --openai-api-base http://localhost:8081/v1 --openai-api-key not-needed'

# Search
alias websearch='f(){ curl -s "http://localhost:8888/search?q=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1")&format=json" | python3 -c "import sys,json; [print(r[\"title\"]) for r in json.load(sys.stdin).get(\"results\",[])[:5]]"; }; f'

# AgentHarness management
alias ah-validate='bash /opt/agentharness/scripts/validate.sh'
alias ah-benchmark='bash /opt/agentharness/scripts/benchmark.sh'
alias ah-daily='bash /opt/agentharness/scripts/daily_improve.sh'
alias ah-weekly='bash /opt/agentharness/scripts/weekly_optimize.sh'
alias ah-reports='ls -lt /opt/agentharness/reports/ | head -10'
alias ah-best='cat /opt/agentharness/best_config.env 2>/dev/null || echo "No benchmark results yet"'
alias ah-doctor='bash /opt/agentharness/scripts/doctor.sh'
alias ah-doctor-fix='bash /opt/agentharness/scripts/doctor.sh --fix'
alias ah-registry='python3 /opt/agentharness/scripts/registry_engine.py list'
alias ah-status='python3 /opt/agentharness/scripts/registry_engine.py status'

# Docker helpers
alias dps='docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
alias dlogs='docker logs --tail 50 -f'
alias drestart='docker restart'
alias dhealth='docker ps --filter "health=unhealthy" --format "{{.Names}}: {{.Status}}"'
ALIASES

    # Add source line to .bashrc if not already present
    if ! grep -q "agentharness/aliases.sh" ~/.bashrc 2>/dev/null; then
        echo "" >> ~/.bashrc
        echo "# AgentHarness aliases" >> ~/.bashrc
        echo "[ -f /opt/agentharness/aliases.sh ] && source /opt/agentharness/aliases.sh" >> ~/.bashrc
        log_ok "Added aliases to ~/.bashrc"
    else
        log_info "Aliases already in .bashrc"
    fi

    log_ok "Aliases written to ${alias_file}"
}

# =============================================================================
# PHASE 10: Validate
# =============================================================================
validate() {
    log_header "Phase 10: Validation"
    bash "${SCRIPT_DIR}/scripts/validate.sh"
}

# =============================================================================
# PHASE 10.5: Install MCP servers
# =============================================================================
setup_mcp_servers() {
    log_header "Phase 10.5: MCP Servers"

    # Check what's already running as MCP
    log_info "Checking for existing MCP servers..."
    local existing_mcp=0

    # Quick probe for any MCP servers already running
    for port in 3000 3001 3333 4000 5000 5001 8000 8001; do
        if curl -sf --max-time 2 -X POST "http://localhost:${port}" \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}' \
            2>/dev/null | grep -q '"result"'; then
            log_ok "MCP server already running on port ${port}"
            ((existing_mcp++))
        fi
    done

    # Check for homelab-mcp-bundle
    if [ -d /opt/homelab-mcp-bundle ] || [ -d /opt/mcp-bundle ]; then
        log_ok "homelab-mcp-bundle already installed"
        ((existing_mcp++))
    fi

    if [ "${existing_mcp}" -gt 0 ]; then
        log_info "Found ${existing_mcp} existing MCP component(s). Running discovery..."
        bash "${SCRIPT_DIR}/scripts/mcp_gateway.sh" || true
        return
    fi

    # Nothing found — offer to install MCP servers
    log_info "No MCP servers detected. Installing recommended MCP servers..."

    ensure_dir /opt/mcp-servers

    # --- 1. Official MCP servers (from modelcontextprotocol org) ---
    # These are lightweight Node.js processes

    local mcp_repos=(
        # repo_url|name|description|requires_service
        "https://github.com/modelcontextprotocol/servers.git|mcp-official|Official MCP servers (filesystem, git, fetch, sqlite)|none"
    )

    # --- 2. Homelab MCP bundle ---
    mcp_repos+=(
        "https://github.com/AI-Engineerings-at/homelab-mcp-bundle.git|homelab-mcp-bundle|Portainer, Grafana, Uptime Kuma, n8n, and more|docker"
    )

    for entry in "${mcp_repos[@]}"; do
        IFS='|' read -r repo name desc requires <<< "${entry}"
        local dest="/opt/mcp-servers/${name}"

        if [ -d "${dest}" ]; then
            log_info "${name}: already cloned"
            continue
        fi

        # Check if required service is available
        if [ "${requires}" = "docker" ] && ! command -v docker &>/dev/null; then
            log_warn "Skipping ${name}: requires Docker"
            continue
        fi

        log_info "Installing: ${name} — ${desc}"

        if git clone --depth 1 "${repo}" "${dest}" 2>/dev/null; then
            log_ok "Cloned: ${name}"

            # Install dependencies
            cd "${dest}"
            if [ -f package.json ]; then
                npm install --quiet 2>/dev/null && log_ok "${name}: npm deps installed" || \
                    log_warn "${name}: npm install failed (install Node.js if needed)"
            elif [ -f requirements.txt ]; then
                pip install --quiet -r requirements.txt 2>/dev/null && log_ok "${name}: pip deps installed" || \
                    log_warn "${name}: pip install failed"
            fi

            # If it has a docker-compose, check if we should start it
            local compose_file
            compose_file=$(ls docker-compose.y*ml compose.y*ml 2>/dev/null | head -1)
            if [ -n "${compose_file}" ]; then
                # Copy .env.example if exists
                [ -f .env.example ] && [ ! -f .env ] && cp .env.example .env

                log_info "${name}: Docker Compose found."
                log_info "  Configure: nano ${dest}/.env"
                log_info "  Start:     cd ${dest} && docker compose up -d"
                log_info "  (Not auto-starting — you need to configure service URLs first)"
            fi
        else
            log_warn "Failed to clone ${name}. Install manually later."
        fi
    done

    # --- 3. Create individual MCP server configs for your specific services ---
    log_info "Creating MCP server configs for your homelab services..."

    # Docker MCP — connects to local Docker socket
    local docker_mcp_dir="/opt/mcp-servers/docker-mcp"
    if [ ! -d "${docker_mcp_dir}" ]; then
        mkdir -p "${docker_mcp_dir}"
        cat > "${docker_mcp_dir}/docker-compose.yml" << 'COMPOSE'
version: '3.8'

services:
  docker-mcp:
    image: mcp/docker-server:latest
    container_name: mcp-docker
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    ports:
      - "127.0.0.1:3100:3000"
    environment:
      - MCP_TRANSPORT=http
    networks:
      - homelab

networks:
  homelab:
    external: true
COMPOSE
        log_ok "Docker MCP config created at ${docker_mcp_dir}"
        log_info "  Start: cd ${docker_mcp_dir} && docker compose up -d"
    fi

    # Filesystem MCP — read/write/search files
    local fs_mcp_dir="/opt/mcp-servers/filesystem-mcp"
    if [ ! -d "${fs_mcp_dir}" ]; then
        mkdir -p "${fs_mcp_dir}"
        cat > "${fs_mcp_dir}/docker-compose.yml" << 'COMPOSE'
version: '3.8'

services:
  filesystem-mcp:
    image: mcp/filesystem-server:latest
    container_name: mcp-filesystem
    restart: unless-stopped
    volumes:
      - /opt:/data/opt:ro
      - /home:/data/home:ro
    ports:
      - "127.0.0.1:3101:3000"
    environment:
      - MCP_TRANSPORT=http
      - ALLOWED_DIRECTORIES=/data/opt,/data/home
    networks:
      - homelab

networks:
  homelab:
    external: true
COMPOSE
        log_ok "Filesystem MCP config created at ${fs_mcp_dir}"
        log_info "  Start: cd ${fs_mcp_dir} && docker compose up -d"
    fi

    # --- 4. Create a master start/stop script ---
    cat > /opt/mcp-servers/start_all.sh << 'STARTALL'
#!/bin/bash
echo "Starting all MCP servers..."
for dir in /opt/mcp-servers/*/; do
    if [ -f "${dir}docker-compose.yml" ] || [ -f "${dir}compose.yml" ]; then
        echo "  Starting: $(basename ${dir})"
        (cd "${dir}" && docker compose up -d 2>/dev/null) || echo "    Failed — check config"
    fi
done
echo "Done. Run 'bash /opt/agentharness/scripts/mcp_gateway.sh' to discover tools."
STARTALL
    chmod +x /opt/mcp-servers/start_all.sh

    cat > /opt/mcp-servers/stop_all.sh << 'STOPALL'
#!/bin/bash
echo "Stopping all MCP servers..."
for dir in /opt/mcp-servers/*/; do
    if [ -f "${dir}docker-compose.yml" ] || [ -f "${dir}compose.yml" ]; then
        echo "  Stopping: $(basename ${dir})"
        (cd "${dir}" && docker compose down 2>/dev/null) || true
    fi
done
echo "Done."
STOPALL
    chmod +x /opt/mcp-servers/stop_all.sh

    # --- 5. Run MCP discovery ---
    log_info "Running MCP discovery..."
    bash "${SCRIPT_DIR}/scripts/mcp_gateway.sh" || true

    # --- Summary ---
    log_header "MCP Setup Summary"
    echo ""
    echo "  MCP server configs: /opt/mcp-servers/"
    echo "  Start all:          bash /opt/mcp-servers/start_all.sh"
    echo "  Stop all:           bash /opt/mcp-servers/stop_all.sh"
    echo "  Discover tools:     bash scripts/mcp_gateway.sh"
    echo ""
    echo "  Before starting MCP servers, configure their .env files:"
    ls -d /opt/mcp-servers/*/.env /opt/mcp-servers/*/.env.example 2>/dev/null | \
        while read -r f; do echo "    ${f}"; done
    echo ""
    echo "  After starting, MCP gateway auto-discovers tools every 6 hours."
    echo ""
}

# =============================================================================
# MAIN
# =============================================================================
main() {
    log_header "AgentHarness Installer"

    # --- Dry run mode ---
    if [ "${DRY_RUN}" = true ]; then
        echo "  DRY RUN — checking what would happen without making changes"
        echo ""
        bash "${SCRIPT_DIR}/scripts/doctor.sh"
        echo ""
        log_info "To install for real, run: ./install.sh"
        log_info "To fix issues first:     ./install.sh --doctor-fix"
        return 0
    fi

    echo "  This will set up your homelab AI infrastructure."
    echo "  Hardware will be auto-detected. Existing tools will be preserved."
    echo ""
    echo "  Flags: ${*:-none}"
    echo ""
    echo "  If anything goes wrong:"
    echo "    ./install.sh --doctor       # See what's broken"
    echo "    ./install.sh --doctor-fix   # Auto-fix safe issues"
    echo "    ./install.sh --phase=N      # Re-run a specific phase"
    echo ""

    # --- Per-phase execution ---
    if [ -n "${RUN_PHASE}" ]; then
        log_info "Running only phase: ${RUN_PHASE}"
        case "${RUN_PHASE}" in
            0)    detect_existing ;;
            1)    install_dependencies ;;
            2)    build_engines ;;
            3)    download_models ;;
            4)    setup_searxng ;;
            5)    setup_services ;;
            6)    run_benchmarks ;;
            7)    setup_env ;;
            8)    setup_scheduler ;;
            8.5)  setup_registry ;;
            9)    setup_aliases ;;
            10)   validate ;;
            10.5) setup_mcp_servers ;;
            11)   bash "${SCRIPT_DIR}/scripts/harden.sh" ;;
            *)    log_error "Unknown phase: ${RUN_PHASE}. Use 0-11." ; exit 1 ;;
        esac
        log_ok "Phase ${RUN_PHASE} complete"
        return 0
    fi

    # --- Full install (each phase logs its own header) ---
    # Each phase checks what already exists and skips accordingly

    detect_existing      # Phase 0
    install_dependencies # Phase 1
    build_engines        # Phase 2
    download_models      # Phase 3
    setup_searxng        # Phase 4
    setup_services       # Phase 5
    run_benchmarks       # Phase 6
    setup_env            # Phase 7
    setup_scheduler      # Phase 8
    setup_registry       # Phase 8.5
    setup_aliases        # Phase 9
    validate             # Phase 10
    setup_mcp_servers    # Phase 10.5

    # Phase 11: Security hardening
    log_header "Phase 11: Security Hardening"
    bash "${SCRIPT_DIR}/scripts/harden.sh"

    log_header "Installation Complete!"
    echo ""
    echo "  Quick start:"
    echo "    source ~/.bashrc        # Load aliases"
    echo "    llm-status              # Check LLM servers"
    echo "    ah-validate             # Full validation"
    echo ""
    echo "  If something isn't working:"
    echo "    ./install.sh --doctor       # Diagnose"
    echo "    ./install.sh --doctor-fix   # Auto-fix"
    echo "    ./install.sh --phase=N      # Re-run phase N"
    echo ""
    echo "  Smart scheduler (every 15 min, network-aware):"
    echo "    OFFLINE (11PM-7:15AM): benchmarks, cleanup, backup, analysis"
    echo "    ONLINE  (7:15AM-11PM): briefing, downloads, syncs, deploys"
    echo ""
    echo "  Reports: /opt/agentharness/reports/"
    echo "  Logs:    /opt/agentharness/logs/"
    echo ""
}

main "$@"
