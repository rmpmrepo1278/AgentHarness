#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# security_audit.sh — Security boundary checks and audit trail
#
# - Verifies OpenClaw Telegram allowFrom is set
# - Checks for exposed ports that shouldn't be public
# - Reviews exec command audit trail
# - Checks Docker socket exposure
# - Discovers existing security measures before adding new ones
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f "${AH_DATA_DIR}/chaguli_paths.env" ] && source "${AH_DATA_DIR}/chaguli_paths.env"

SECURITY_REPORT="${AH_REPORTS_DIR}/security_$(timestamp).md"
AUDIT_LOG="${AH_LOGS_DIR}/exec_audit.log"

main() {
    log_header "Security Audit"

    ensure_dir "${AH_REPORTS_DIR}"
    ensure_dir "${AH_LOGS_DIR}"

    cat > "${SECURITY_REPORT}" << EOF
# Security Audit Report
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    local issues=0

    # --- 1. OpenClaw Telegram access control ---
    echo "## OpenClaw Access Control" >> "${SECURITY_REPORT}"
    if [ -f "${OPENCLAW_CONFIG:-}" ]; then
        local has_allowfrom
        has_allowfrom=$(python3 -c "
import json
cfg = json.load(open('${OPENCLAW_CONFIG}'))
tg = cfg.get('channels', {}).get('telegram', {})
af = tg.get('allowFrom', tg.get('allow_from', []))
if af:
    print(f'CONFIGURED: {af}')
else:
    print('NOT SET')
" 2>/dev/null || echo "UNKNOWN")

        if [[ "${has_allowfrom}" == "NOT SET" ]]; then
            echo "- **CRITICAL**: Telegram allowFrom is not set — anyone can message Chaguli" >> "${SECURITY_REPORT}"
            ((issues++))
        else
            echo "- OK: Telegram allowFrom: ${has_allowfrom}" >> "${SECURITY_REPORT}"
        fi
    else
        echo "- WARN: OpenClaw config not found — cannot verify access control" >> "${SECURITY_REPORT}"
    fi
    echo "" >> "${SECURITY_REPORT}"

    # --- 2. Exposed ports ---
    echo "## Exposed Ports" >> "${SECURITY_REPORT}"
    # Find ports bound to 0.0.0.0 (accessible from network)
    local exposed
    exposed=$(ss -tlnp 2>/dev/null | grep "0.0.0.0:" | awk '{print $4}' | sed 's/0.0.0.0://' | sort -n || true)
    if [ -n "${exposed}" ]; then
        echo "Ports bound to 0.0.0.0 (network-accessible):" >> "${SECURITY_REPORT}"
        while read -r port; do
            local process
            process=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP '(?<=users:\(\().*?(?=,)' | head -1 || echo "unknown")
            echo "- :${port} — ${process}" >> "${SECURITY_REPORT}"
        done <<< "${exposed}"
    fi
    echo "" >> "${SECURITY_REPORT}"

    # --- 3. Docker socket access ---
    echo "## Docker Socket" >> "${SECURITY_REPORT}"
    local docker_sock="/var/run/docker.sock"
    if [ -S "${docker_sock}" ]; then
        local sock_perms
        sock_perms=$(stat -c '%a %U:%G' "${docker_sock}" 2>/dev/null || echo "unknown")
        echo "- Socket: ${docker_sock} (${sock_perms})" >> "${SECURITY_REPORT}"

        # Check which containers have docker.sock mounted
        local sock_containers
        sock_containers=$(docker ps --format '{{.Names}}' 2>/dev/null | while read -r c; do
            docker inspect --format '{{range .Mounts}}{{if eq .Source "/var/run/docker.sock"}}{{$.Name}}{{end}}{{end}}' "${c}" 2>/dev/null
        done | grep -v '^$' || true)

        if [ -n "${sock_containers}" ]; then
            echo "- Containers with docker.sock access:" >> "${SECURITY_REPORT}"
            while read -r c; do
                echo "  - ${c}" >> "${SECURITY_REPORT}"
            done <<< "${sock_containers}"
        fi
    fi
    echo "" >> "${SECURITY_REPORT}"

    # --- 4. Secrets in environment ---
    echo "## Secret Hygiene" >> "${SECURITY_REPORT}"
    # Check for .env files readable by others
    find /opt /home -maxdepth 4 -name ".env" -type f 2>/dev/null | while read -r envfile; do
        local perms
        perms=$(stat -c '%a' "${envfile}" 2>/dev/null || echo "000")
        if [ "${perms: -1}" != "0" ]; then
            echo "- WARN: ${envfile} is world-readable (${perms})" >> "${SECURITY_REPORT}"
            ((issues++)) || true
        fi
    done
    echo "" >> "${SECURITY_REPORT}"

    # --- 5. Audit trail setup ---
    echo "## Exec Audit Trail" >> "${SECURITY_REPORT}"
    if [ -f "${AUDIT_LOG}" ]; then
        local audit_lines
        audit_lines=$(wc -l < "${AUDIT_LOG}")
        local recent
        recent=$(tail -5 "${AUDIT_LOG}" 2>/dev/null || echo "(empty)")
        echo "- Audit log: ${AUDIT_LOG} (${audit_lines} entries)" >> "${SECURITY_REPORT}"
        echo "- Recent:" >> "${SECURITY_REPORT}"
        echo '```' >> "${SECURITY_REPORT}"
        echo "${recent}" >> "${SECURITY_REPORT}"
        echo '```' >> "${SECURITY_REPORT}"
    else
        echo "- Audit log not yet created. Will be populated as tools execute." >> "${SECURITY_REPORT}"
        touch "${AUDIT_LOG}"
    fi
    echo "" >> "${SECURITY_REPORT}"

    # --- 6. Existing security measures ---
    echo "## Existing Security Measures" >> "${SECURITY_REPORT}"
    # Firewall
    if command -v ufw &>/dev/null; then
        local ufw_status
        ufw_status=$(sudo ufw status 2>/dev/null | head -1 || echo "unknown")
        echo "- UFW: ${ufw_status}" >> "${SECURITY_REPORT}"
    elif command -v iptables &>/dev/null; then
        local rules
        rules=$(sudo iptables -L 2>/dev/null | wc -l || echo "0")
        echo "- iptables: ${rules} rules" >> "${SECURITY_REPORT}"
    fi

    # fail2ban
    if command -v fail2ban-client &>/dev/null; then
        local f2b_status
        f2b_status=$(sudo fail2ban-client status 2>/dev/null | head -3 || echo "not running")
        echo "- fail2ban: installed" >> "${SECURITY_REPORT}"
    fi

    # SSH config
    if [ -f /etc/ssh/sshd_config ]; then
        local root_login
        root_login=$(grep -i "^PermitRootLogin" /etc/ssh/sshd_config 2>/dev/null || echo "not set")
        local pass_auth
        pass_auth=$(grep -i "^PasswordAuthentication" /etc/ssh/sshd_config 2>/dev/null || echo "not set")
        echo "- SSH: ${root_login}, ${pass_auth}" >> "${SECURITY_REPORT}"
    fi
    echo "" >> "${SECURITY_REPORT}"

    # Summary
    cat >> "${SECURITY_REPORT}" << EOF

---
**Issues found**: ${issues}
EOF

    log_ok "Security audit: ${SECURITY_REPORT} (${issues} issue(s))"

    [ "${issues}" -gt 0 ] && bash "${SCRIPT_DIR}/alert.sh" WARN "Security audit found ${issues} issue(s). See ${SECURITY_REPORT}"
}

main "$@"
