#!/usr/bin/env bash
# =============================================================================
# common.sh — Shared utilities for all AgentHarness scripts
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

# Ensure a directory exists with correct ownership
ensure_dir() {
    local dir="$1"
    if [ ! -d "${dir}" ]; then
        sudo mkdir -p "${dir}"
        sudo chown "$USER:$USER" "${dir}"
    fi
}

# Paths used across scripts
AGENTHARNESS_DIR="/opt/agentharness"
MODEL_DIR="/opt/models"
REPORT_DIR="/opt/agentharness/reports"
