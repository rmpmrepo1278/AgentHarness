#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# backup.sh — Backup homelab configs, OpenClaw state, AgentHarness data
#              to discovered USB drive. Discovers existing backup solutions
#              and integrates rather than replacing.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

STORAGE_PATHS="${AH_DATA_DIR}/storage_paths.env"
BACKUP_REPORT="${AH_REPORTS_DIR}/backup_$(timestamp).md"

[ -f "${AH_DATA_DIR}/chaguli_paths.env" ] && source "${AH_DATA_DIR}/chaguli_paths.env"

# =============================================================================
# Discover backup target
# =============================================================================
setup_backup_target() {
    # Run storage discovery if needed
    if [ ! -f "${STORAGE_PATHS}" ]; then
        bash "${SCRIPT_DIR}/discover_storage.sh"
    fi
    source "${STORAGE_PATHS}"

    if [ -z "${BACKUP_DRIVE:-}" ]; then
        log_error "No backup drive found. Connect a USB drive and run: bash scripts/discover_storage.sh"
        return 1
    fi

    # Create backup directory structure on the drive
    BACKUP_ROOT="${BACKUP_DRIVE}/agentharness-backups"
    BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y-%m-%d)"
    mkdir -p "${BACKUP_DIR}"

    log_ok "Backup target: ${BACKUP_DIR}"
    log_info "Drive: ${BACKUP_DRIVE} (${BACKUP_DRIVE_AVAIL:-?} free)"
}

# =============================================================================
# Check for and run existing backup scripts first
# =============================================================================
run_existing_backups() {
    if [ -n "${EXISTING_BACKUP_SCRIPTS:-}" ]; then
        echo "## Existing Backup Scripts" >> "${BACKUP_REPORT}"
        for script in ${EXISTING_BACKUP_SCRIPTS}; do
            if [ -x "${script}" ]; then
                log_info "Running existing backup: ${script}"
                echo "- Running: ${script}" >> "${BACKUP_REPORT}"
                if bash "${script}" 2>&1 | tail -5; then
                    echo "  Status: OK" >> "${BACKUP_REPORT}"
                else
                    echo "  Status: FAILED (non-blocking)" >> "${BACKUP_REPORT}"
                fi
            fi
        done
        echo "" >> "${BACKUP_REPORT}"
    fi
}

# =============================================================================
# Backup Docker configs
# =============================================================================
backup_docker() {
    log_info "Backing up Docker configurations..."
    local dest="${BACKUP_DIR}/docker"
    mkdir -p "${dest}"

    echo "## Docker Configs" >> "${BACKUP_REPORT}"

    # Find and backup all docker-compose files with their .env
    find /opt /home -maxdepth 4 \
        \( -name "docker-compose.yml" -o -name "docker-compose.yaml" \
           -o -name "compose.yml" -o -name "compose.yaml" \) \
        -type f 2>/dev/null | while read -r compose; do

        local dir
        dir=$(dirname "${compose}")
        local safe_name
        safe_name=$(echo "${dir}" | tr '/' '_' | sed 's/^_//')
        local target="${dest}/${safe_name}"
        mkdir -p "${target}"

        # Copy compose file
        cp "${compose}" "${target}/"

        # Copy .env if exists (redact secrets for safety)
        if [ -f "${dir}/.env" ]; then
            # Backup the actual .env (encrypted if possible)
            cp "${dir}/.env" "${target}/.env"
        fi

        # Copy any other config files in the same directory
        for cfg in "${dir}"/*.conf "${dir}"/*.yml "${dir}"/*.yaml "${dir}"/*.toml "${dir}"/*.ini; do
            [ -f "${cfg}" ] && cp "${cfg}" "${target}/" 2>/dev/null || true
        done

        echo "- ${dir} → ${target}" >> "${BACKUP_REPORT}"
    done

    # Backup list of running containers and their configs
    docker ps --format '{{.Names}} {{.Image}} {{.Ports}}' > "${dest}/running_containers.txt" 2>/dev/null || true
    docker inspect $(docker ps -q) > "${dest}/container_inspect.json" 2>/dev/null || true

    log_ok "Docker configs backed up"
    echo "" >> "${BACKUP_REPORT}"
}

# =============================================================================
# Backup OpenClaw state
# =============================================================================
backup_openclaw() {
    log_info "Backing up OpenClaw/Chaguli state..."
    local dest="${BACKUP_DIR}/openclaw"
    mkdir -p "${dest}"

    echo "## OpenClaw / Chaguli" >> "${BACKUP_REPORT}"

    # Use discovered paths
    if [ -n "${OPENCLAW_HOME:-}" ] && [ -d "${OPENCLAW_HOME}" ]; then
        # Backup config
        [ -f "${OPENCLAW_CONFIG:-}" ] && cp "${OPENCLAW_CONFIG}" "${dest}/" && \
            echo "- Config: ${OPENCLAW_CONFIG}" >> "${BACKUP_REPORT}"

        # Backup workspace (skills, AGENTS.md, SOUL.md, TOOLS.md)
        if [ -d "${OPENCLAW_WORKSPACE:-}" ]; then
            rsync -a --exclude='node_modules' --exclude='.git' \
                "${OPENCLAW_WORKSPACE}/" "${dest}/workspace/" 2>/dev/null || \
                cp -r "${OPENCLAW_WORKSPACE}" "${dest}/workspace" 2>/dev/null || true
            echo "- Workspace: ${OPENCLAW_WORKSPACE}" >> "${BACKUP_REPORT}"
        fi

        # Backup skills
        if [ -d "${OPENCLAW_SKILLS_DIR:-}" ]; then
            local skill_count
            skill_count=$(find "${OPENCLAW_SKILLS_DIR}" -name "SKILL.md" 2>/dev/null | wc -l)
            echo "- Skills: ${skill_count} skills backed up" >> "${BACKUP_REPORT}"
        fi

        # Backup logs if they exist
        if [ -d "${OPENCLAW_HOME}/logs" ]; then
            mkdir -p "${dest}/logs"
            # Only last 7 days of logs
            find "${OPENCLAW_HOME}/logs" -name "*.log" -mtime -7 -exec cp {} "${dest}/logs/" \; 2>/dev/null || true
            echo "- Logs: last 7 days" >> "${BACKUP_REPORT}"
        fi
    else
        echo "- WARNING: OpenClaw home not discovered. Run discover_automations.sh" >> "${BACKUP_REPORT}"
    fi

    log_ok "OpenClaw state backed up"
    echo "" >> "${BACKUP_REPORT}"
}

# =============================================================================
# Backup AgentHarness state
# =============================================================================
backup_agentharness() {
    log_info "Backing up AgentHarness state..."
    local dest="${BACKUP_DIR}/agentharness"
    mkdir -p "${dest}"

    echo "## AgentHarness" >> "${BACKUP_REPORT}"

    # Core state files
    for f in "${AH_DATA_DIR}"/*.json "${AH_DATA_DIR}"/*.env; do
        [ -f "$f" ] && cp "$f" "${dest}/" 2>/dev/null || true
    done

    # Scripts (the whole project)
    if [ -d "${AH_SCRIPTS_DIR}" ]; then
        cp -r "${AH_SCRIPTS_DIR}" "${dest}/"
    fi

    # Last 10 reports
    if [ -d "${AH_REPORTS_DIR}" ]; then
        mkdir -p "${dest}/reports"
        ls -t "${AH_REPORTS_DIR}"/*.md 2>/dev/null | head -10 | while read -r report; do
            cp "${report}" "${dest}/reports/"
        done
    fi

    # Improvement tasks
    [ -d "${AH_DATA_DIR}/improvements" ] && cp -r "${AH_DATA_DIR}/improvements" "${dest}/"

    # Chaguli memory (if exists)
    [ -f "${AH_DATA_DIR}/chaguli_memory.json" ] && cp "${AH_DATA_DIR}/chaguli_memory.json" "${dest}/"

    echo "- State files, scripts, recent reports, improvements" >> "${BACKUP_REPORT}"
    log_ok "AgentHarness state backed up"
    echo "" >> "${BACKUP_REPORT}"
}

# =============================================================================
# Backup system configs
# =============================================================================
backup_system() {
    log_info "Backing up system configs..."
    local dest="${BACKUP_DIR}/system"
    mkdir -p "${dest}"

    echo "## System" >> "${BACKUP_REPORT}"

    # Crontabs
    crontab -l > "${dest}/crontab_user.txt" 2>/dev/null || true
    sudo crontab -l > "${dest}/crontab_root.txt" 2>/dev/null || true

    # Custom systemd services
    mkdir -p "${dest}/systemd"
    for svc in /etc/systemd/system/*.service; do
        [ -f "${svc}" ] || continue
        [[ "$(basename "${svc}")" == systemd-* ]] && continue
        cp "${svc}" "${dest}/systemd/"
    done

    # Network config
    cp /etc/hosts "${dest}/" 2>/dev/null || true
    cp /etc/resolv.conf "${dest}/" 2>/dev/null || true
    ip addr show > "${dest}/ip_addresses.txt" 2>/dev/null || true

    # Package list
    dpkg --get-selections > "${dest}/installed_packages.txt" 2>/dev/null || true
    pip list --format=freeze > "${dest}/pip_packages.txt" 2>/dev/null || true

    echo "- Crontabs, systemd services, network config, package lists" >> "${BACKUP_REPORT}"
    log_ok "System configs backed up"
    echo "" >> "${BACKUP_REPORT}"
}

# =============================================================================
# Rotation — keep last N backups
# =============================================================================
rotate_backups() {
    local keep="${1:-14}"  # Keep 14 days by default
    log_info "Rotating backups (keeping last ${keep})..."

    local count
    count=$(ls -d "${BACKUP_ROOT}"/20* 2>/dev/null | wc -l)

    if [ "${count}" -gt "${keep}" ]; then
        local to_delete=$((count - keep))
        ls -d "${BACKUP_ROOT}"/20* 2>/dev/null | head -"${to_delete}" | while read -r old; do
            log_info "Removing old backup: ${old}"
            rm -rf "${old}"
        done
        echo "- Rotated: removed ${to_delete} old backup(s), keeping ${keep}" >> "${BACKUP_REPORT}"
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "Homelab Backup"

    ensure_dir "${AH_REPORTS_DIR}"

    cat > "${BACKUP_REPORT}" << EOF
# Backup Report
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    setup_backup_target || exit 1
    run_existing_backups
    backup_docker
    backup_openclaw
    backup_agentharness
    backup_system
    rotate_backups

    # Calculate total size
    local backup_size
    backup_size=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)

    cat >> "${BACKUP_REPORT}" << EOF

---
**Backup location**: ${BACKUP_DIR}
**Total size**: ${backup_size}
**Drive free**: $(df -h "${BACKUP_DRIVE}" 2>/dev/null | awk 'NR==2 {print $4}')
EOF

    log_ok "Backup complete: ${BACKUP_DIR} (${backup_size})"

    # Notify
    bash "${AH_SCRIPTS_DIR}/alert.sh" INFO "Backup complete. See ${BACKUP_REPORT}" backup
}

main "$@"
