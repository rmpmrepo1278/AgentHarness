#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# harden.sh — Security hardening for AgentHarness
#
# Run after install.sh to lock down the deployment.
# Fixes: file permissions, secret protection, OpenClaw access control,
#        GitHub deploy safety, backup encryption.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f "${AH_DATA_DIR}/chaguli_paths.env" ] && source "${AH_DATA_DIR}/chaguli_paths.env"

SECURITY_LOG="${AH_LOGS_DIR}/harden.log"
ISSUES=0
FIXED=0

fix() {
    local desc="$1"
    local cmd="$2"
    log_info "Fixing: ${desc}"
    if eval "${cmd}" 2>>"${SECURITY_LOG}"; then
        log_ok "  Fixed"
        ((FIXED++))
    else
        log_error "  Failed — run manually: ${cmd}"
    fi
}

main() {
    log_header "Security Hardening"
    ensure_dir "${AH_LOGS_DIR}"

    echo "$(date -Iseconds) — Hardening run started" >> "${SECURITY_LOG}"

    # =========================================================================
    # 1. Lock down file permissions on secrets
    # =========================================================================
    log_info "[1/8] Locking down secret files..."

    for secret_file in "${AH_DATA_DIR}/.env" \
                       "${AH_DATA_DIR}/discovered_config.json" \
                       "${AH_DATA_DIR}/openclaw_paths.env" \
                       "${AH_DATA_DIR}/storage_paths.env"; do
        if [ -f "${secret_file}" ]; then
            local perms
            perms=$(stat -c '%a' "${secret_file}" 2>/dev/null || echo "777")
            if [ "${perms}" != "600" ]; then
                fix "Set ${secret_file} to 600 (owner-only)" \
                    "chmod 600 ${secret_file}"
            fi
        fi
    done

    # All .env files in Docker compose directories
    find /opt /home -maxdepth 4 -name ".env" -type f 2>/dev/null | while read -r envfile; do
        local perms
        perms=$(stat -c '%a' "${envfile}" 2>/dev/null || echo "777")
        if [ "${perms: -1}" != "0" ]; then
            fix "Set ${envfile} to 600" "chmod 600 ${envfile}"
        fi
    done

    # =========================================================================
    # 2. Verify OpenClaw Telegram allowFrom
    # =========================================================================
    log_info "[2/8] Checking OpenClaw Telegram access control..."

    if [ -f "${OPENCLAW_CONFIG:-}" ]; then
        local allow_status
        allow_status=$(python3 -c "
import json
cfg = json.load(open('${OPENCLAW_CONFIG}'))
tg = cfg.get('channels', {}).get('telegram', {})
af = tg.get('allowFrom', tg.get('allow_from', []))
if af:
    print(f'SET:{af}')
else:
    print('NOT_SET')
" 2>/dev/null || echo "UNKNOWN")

        if [[ "${allow_status}" == "NOT_SET" ]]; then
            log_error "CRITICAL: Telegram allowFrom is NOT set!"
            echo ""
            echo "  Anyone who knows your bot token can control your homelab."
            echo "  Fix: Edit ${OPENCLAW_CONFIG} and add your Telegram user ID:"
            echo ""
            echo '  "channels": {'
            echo '    "telegram": {'
            echo '      "botToken": "YOUR_TOKEN",'
            echo '      "allowFrom": [YOUR_TELEGRAM_USER_ID]'
            echo '    }'
            echo '  }'
            echo ""
            echo "  Find your Telegram user ID: message @userinfobot on Telegram"
            echo ""
            ((ISSUES++))
        else
            log_ok "  allowFrom is configured: ${allow_status#SET:}"
        fi
    else
        log_warn "  OpenClaw config not found — cannot verify"
    fi

    # =========================================================================
    # 3. Sandbox the GitHub auto-deploy
    # =========================================================================
    log_info "[3/8] Hardening GitHub auto-deploy..."

    # Create a restricted deploy directory with its own user (if possible)
    if [ ! -d /opt/deployments ]; then
        mkdir -p /opt/deployments
    fi
    chmod 755 /opt/deployments

    # Add stronger safety blocks to github_deploy.sh
    # The script already blocks basic dangerous patterns, but let's verify
    if grep -q "rm -rf" "${SCRIPT_DIR}/github_deploy.sh" 2>/dev/null; then
        log_ok "  github_deploy.sh has safety blocks"
    fi

    # Create a deploy policy file that the deploy script reads
    cat > "${AH_DATA_DIR}/deploy_policy.json" << 'POLICY'
{
  "blocked_commands": [
    "rm -rf /",
    "rm -rf /*",
    "dd if=",
    "mkfs.",
    ":(){",
    "curl|sh",
    "curl|bash",
    "wget|sh",
    "wget|bash",
    "chmod 777 /",
    "docker run --privileged",
    "docker run -v /:/",
    "--net=host --pid=host",
    "iptables -F",
    "ufw disable",
    "systemctl disable",
    "passwd",
    "useradd",
    "visudo",
    "crontab -r"
  ],
  "blocked_docker_flags": [
    "--privileged",
    "-v /:/",
    "--pid=host",
    "--net=host",
    "--cap-add=ALL"
  ],
  "max_execution_time_seconds": 600,
  "require_review_for": [
    "docker run",
    "systemctl",
    "apt install",
    "pip install"
  ],
  "allowed_install_methods": [
    "docker compose up -d",
    "pip install -r requirements.txt",
    "npm install",
    "make"
  ]
}
POLICY
    chmod 644 "${AH_DATA_DIR}/deploy_policy.json"
    log_ok "  Deploy policy written to ${AH_DATA_DIR}/deploy_policy.json"

    # =========================================================================
    # 4. Encrypt backup secrets
    # =========================================================================
    log_info "[4/8] Setting up backup encryption..."

    if command -v gpg &>/dev/null; then
        # Create a symmetric encryption wrapper for backups
        cat > "${AH_SCRIPTS_DIR}/encrypt_backup.sh" << 'ENCRYPT'
#!/bin/bash
# Encrypt sensitive backup files with a passphrase
# Usage: encrypt_backup.sh <backup_dir>
BACKUP_DIR="${1:?Usage: encrypt_backup.sh <backup_dir>}"
PASSPHRASE_FILE="${AH_DATA_DIR:-.}/.backup_passphrase"

if [ ! -f "${PASSPHRASE_FILE}" ]; then
    openssl rand -base64 32 > "${PASSPHRASE_FILE}"
    chmod 600 "${PASSPHRASE_FILE}"
    echo "Generated backup passphrase at ${PASSPHRASE_FILE} — SAVE THIS SOMEWHERE SAFE"
fi

# Encrypt .env files in the backup
find "${BACKUP_DIR}" -name ".env" -o -name "*.key" -o -name "*secret*" -o -name "*token*" | while read -r f; do
    gpg --batch --yes --passphrase-file "${PASSPHRASE_FILE}" -c "${f}" && rm "${f}"
    echo "Encrypted: ${f}"
done
ENCRYPT
        chmod 700 "${AH_SCRIPTS_DIR}/encrypt_backup.sh"
        log_ok "  Backup encryption script created"
    else
        log_warn "  gpg not installed — backups will be unencrypted"
        echo "  Fix: sudo apt-get install -y gnupg"
    fi

    # =========================================================================
    # 5. Create exec audit trail
    # =========================================================================
    log_info "[5/8] Setting up command audit trail..."

    local audit_log="${AH_LOGS_DIR}/exec_audit.log"
    touch "${audit_log}"
    chmod 600 "${audit_log}"

    # If OpenClaw supports exec hooks, log all commands
    # For now, create a wrapper skill that logs commands
    if [ -n "${OPENCLAW_SKILLS_DIR:-}" ]; then
        local audit_skill_dir="${OPENCLAW_SKILLS_DIR}/agentharness-audit"
        mkdir -p "${audit_skill_dir}"
        cat > "${audit_skill_dir}/SKILL.md" << 'AUDITSKILL'
---
name: agentharness-audit
description: Security audit trail — log all exec commands before running them
requires:
  binaries: ["bash"]
---

# Exec Audit Trail

IMPORTANT: Before running ANY exec command that modifies the system (not just reads), log it first:

```bash
echo "$(date -Iseconds) | $(whoami) | COMMAND_HERE" >> ${AH_LOGS_DIR}/exec_audit.log
```

Commands that MUST be logged:
- docker restart/stop/start/rm
- systemctl restart/stop/start
- rm, mv, cp of config files
- Any install/deploy commands
- apt/pip/npm install
- File edits to /etc/ or service configs
- iptables/ufw changes

Commands that DON'T need logging:
- docker ps, docker logs (read-only)
- curl to health endpoints (read-only)
- cat, ls, df, free (read-only)
- Reading log files
AUDITSKILL
        log_ok "  Audit trail skill installed"
    fi

    # =========================================================================
    # 6. Restrict ClawHub skill installation
    # =========================================================================
    log_info "[6/8] ClawHub skill safety..."

    echo ""
    echo "  RECOMMENDATION: Before installing any ClawHub skill:"
    echo "    1. Check the source repo on GitHub"
    echo "    2. Read the SKILL.md before installing"
    echo "    3. Prefer skills from the VoltAgent/awesome-openclaw-skills curated list"
    echo "    4. Never install skills that ask for API keys or tokens in their SKILL.md"
    echo "    5. Run: clawhub install SKILL --dry-run (if supported)"
    echo ""

    # Create a vetted skills allowlist
    cat > "${AH_DATA_DIR}/clawhub_allowlist.txt" << 'ALLOWLIST'
# ClawHub Skills Allowlist
# Only install skills listed here. Add new ones after reviewing source.
capability-evolver
tavily
memory-context
ALLOWLIST
    log_ok "  Allowlist created at ${AH_DATA_DIR}/clawhub_allowlist.txt"

    # =========================================================================
    # 7. Network exposure check
    # =========================================================================
    log_info "[7/8] Checking network exposure..."

    # Find services bound to 0.0.0.0 that should be localhost only
    local dangerous_binds=""
    dangerous_binds=$(ss -tlnp 2>/dev/null | grep "0.0.0.0:" | awk '{print $4}' | sed 's/0.0.0.0://' | sort -n)

    if [ -n "${dangerous_binds}" ]; then
        echo ""
        echo "  Services bound to 0.0.0.0 (accessible from network):"
        while read -r port; do
            local proc
            proc=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP '(?<=users:\(\().*?(?=,)' | head -1 || echo "?")
            echo "    :${port} — ${proc}"
        done <<< "${dangerous_binds}"
        echo ""
        echo "  Consider binding internal services to 127.0.0.1 instead."
        echo "  LLM servers, SearXNG, and management APIs should NOT be exposed."
    fi

    # =========================================================================
    # 8. SSH hardening check
    # =========================================================================
    log_info "[8/8] SSH configuration..."

    if [ -f /etc/ssh/sshd_config ]; then
        local root_login
        root_login=$(grep -i "^PermitRootLogin" /etc/ssh/sshd_config | awk '{print $2}')
        local pass_auth
        pass_auth=$(grep -i "^PasswordAuthentication" /etc/ssh/sshd_config | awk '{print $2}')

        [ "${root_login}" = "yes" ] && log_warn "  SSH allows root login — consider: PermitRootLogin no" && ((ISSUES++))
        [ "${pass_auth}" = "yes" ] && log_warn "  SSH allows password auth — consider: PasswordAuthentication no" && ((ISSUES++))
        [ "${root_login}" != "yes" ] && [ "${pass_auth}" != "yes" ] && log_ok "  SSH config looks good"
    fi

    # =========================================================================
    # Summary
    # =========================================================================
    log_header "Hardening Summary"

    echo "  Fixed: ${FIXED}"
    echo "  Remaining issues: ${ISSUES}"
    echo ""

    if [ "${ISSUES}" -gt 0 ]; then
        log_warn "Review the issues above and fix manually."
    else
        log_ok "System hardened. Run security_audit.sh periodically to verify."
    fi

    echo ""
    echo "  Ongoing security:"
    echo "    • security_audit.sh runs weekly via scheduler"
    echo "    • exec_audit.log tracks system-modifying commands"
    echo "    • deploy_policy.json blocks dangerous deploy patterns"
    echo "    • clawhub_allowlist.txt restricts skill installation"
    echo "    • backup encryption via encrypt_backup.sh"
    echo ""
}

main "$@"
