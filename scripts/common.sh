#!/usr/bin/env bash
# =============================================================================
# common.sh — Shared utilities for all AgentHarness scripts
#
# Sources state.json (written by discovery) and exports AH_* variables so
# every script that does `source common.sh` gets discovered paths automatically.
# No hardcoded paths — everything comes from state.json.
# =============================================================================

# Colors (only if terminal supports it)
if [ -t 1 ] && command -v tput &>/dev/null; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
fi

log_info()   { echo "${BLUE}[INFO]${RESET} $*"; }
log_ok()     { echo "${GREEN}[OK]${RESET} $*"; }
log_warn()   { echo "${YELLOW}[WARN]${RESET} $*"; }
log_error()  { echo "${RED}[ERROR]${RESET} $*" >&2; }
log_header() {
    echo ""
    echo "${BOLD}=========================================${RESET}"
    echo "${BOLD}  $*${RESET}"
    echo "${BOLD}=========================================${RESET}"
    echo ""
}

# Timestamp for reports
timestamp() { date '+%Y-%m-%d_%H-%M-%S'; }

# Check if running as root (warn, don't require)
check_root_warn() {
    if [ "$(id -u)" -eq 0 ]; then
        log_warn "Running as root. Some operations will run without sudo."
    fi
}

# Ensure a directory exists with correct ownership.
# Only uses sudo when the parent directory is not writable by the current user.
ensure_dir() {
    local dir="$1"
    if [ ! -d "${dir}" ]; then
        local parent
        parent="$(dirname "${dir}")"
        if [ -w "${parent}" ] || [ "$(id -u)" -eq 0 ]; then
            mkdir -p "${dir}"
        else
            sudo mkdir -p "${dir}"
            sudo chown "$USER:$USER" "${dir}"
        fi
    fi
}

# =============================================================================
# Discovery: locate state.json and load AH_* environment variables
# =============================================================================

# _ah_find_state — locate state.json on disk.
# Returns the path to state.json on stdout, or exits 1 if not found.
_ah_find_state() {
    local state_file

    # 1. AH_DATA_DIR env var
    if [ -n "${AH_DATA_DIR:-}" ] && [ -f "${AH_DATA_DIR}/state.json" ]; then
        echo "${AH_DATA_DIR}/state.json"
        return 0
    fi

    # 2. Relative to this script: ../data/state.json
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    state_file="${script_dir}/../data/state.json"
    if [ -f "${state_file}" ]; then
        echo "$(cd "$(dirname "${state_file}")" && pwd)/state.json"
        return 0
    fi

    # 3. Common locations
    local loc
    for loc in \
        "${HOME}/agentharness/data/state.json" \
        "/opt/agentharness/data/state.json" \
        "${HOME}/.agentharness/data/state.json" \
        "${HOME}/.local/share/agentharness/data/state.json"; do
        if [ -f "${loc}" ]; then
            echo "${loc}"
            return 0
        fi
    done

    return 1
}

# _ah_load_paths — parse state.json and export AH_* variables.
# Uses python3 to read JSON and emit shell export statements.
_ah_load_paths() {
    local state_file
    state_file="$(_ah_find_state)" || {
        log_error "Cannot find state.json. Run discovery first:"
        log_error "  python3 -m core.discovery.engine"
        log_error "Or set AH_DATA_DIR to the directory containing state.json."
        return 1
    }

    local exports
    exports="$(python3 -c "
import json, sys, os

try:
    with open('${state_file}', 'r') as f:
        state = json.load(f)
except (json.JSONDecodeError, OSError) as e:
    print(f'echo \"[ERROR] Failed to parse {\"${state_file}\"}: {e}\" >&2', file=sys.stdout)
    sys.exit(1)

paths = state.get('paths', {})
if not paths:
    print('echo \"[ERROR] state.json has no paths section\" >&2', file=sys.stdout)
    sys.exit(1)

# Map state.json keys to AH_* variable names
for key, value in paths.items():
    var_name = 'AH_' + key.upper()
    # Shell-safe: only export if value has no dangerous chars
    safe = value.replace(\"'\", \"'\\\\''\")
    print(f\"export {var_name}='{safe}'\")
" 2>&1)" || {
        log_error "Failed to parse state.json with python3."
        return 1
    }

    eval "${exports}"
}

# =============================================================================
# Auto-load on source: every script that sources common.sh gets AH_* vars
# =============================================================================
_ah_load_paths

# Legacy compatibility variables (used by older scripts)
AGENTHARNESS_DIR="${AH_INSTALL_DIR:-}"
MODEL_DIR="${AH_MODEL_DIR:-}"
REPORT_DIR="${AH_REPORTS_DIR:-}"

# =============================================================================
# Source .env file if it exists (for secrets, tokens, custom overrides)
# =============================================================================
if [ -n "${AH_DATA_DIR:-}" ] && [ -f "${AH_DATA_DIR}/.env" ]; then
    # shellcheck disable=SC1091
    source "${AH_DATA_DIR}/.env"
elif [ -n "${AH_INSTALL_DIR:-}" ] && [ -f "${AH_INSTALL_DIR}/.env" ]; then
    # shellcheck disable=SC1091
    source "${AH_INSTALL_DIR}/.env"
fi
