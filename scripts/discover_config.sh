#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# discover_config.sh — Discover existing configs on the system
#
# Scans for API keys, service URLs, tokens, and settings already configured
# in .env files, Docker containers, environment variables, config files, etc.
# Generates /opt/agentharness/.env by merging discovered values with template.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

DISCOVERED="/opt/agentharness/discovered_config.json"
ENV_FILE="/opt/agentharness/.env"

# Associative array for discovered values
declare -A CONFIG

# =============================================================================
# Discovery functions
# =============================================================================

# -----------------------------------------------------------------------------
# Scan all .env files on the system
# -----------------------------------------------------------------------------
scan_env_files() {
    log_info "Scanning .env files..."

    local env_files
    env_files=$(find /opt /home /root /etc \
        -maxdepth 4 \
        -name ".env" -o -name ".env.local" -o -name ".env.production" \
        2>/dev/null | head -50)

    while IFS= read -r envfile; do
        [ -z "${envfile}" ] && continue
        [ ! -f "${envfile}" ] && continue

        log_info "  Found: ${envfile}"

        while IFS='=' read -r key value; do
            # Skip comments and empty lines
            [[ "${key}" =~ ^#.*$ ]] && continue
            [ -z "${key}" ] && continue
            # Strip quotes from value
            value=$(echo "${value}" | sed 's/^["'\''"]//; s/["'\''"]$//' | xargs)
            [ -z "${value}" ] && continue

            # Map known keys
            case "${key}" in
                GROQ_API_KEY|groq_api_key)
                    [ -z "${CONFIG[GROQ_API_KEY]:-}" ] && CONFIG[GROQ_API_KEY]="${value}" && \
                        log_ok "    Found GROQ_API_KEY in ${envfile}"
                    ;;
                OPENAI_API_KEY|openai_api_key)
                    # Some tools store Groq key as OPENAI_API_KEY with groq base URL
                    if echo "${value}" | grep -q "^gsk_"; then
                        [ -z "${CONFIG[GROQ_API_KEY]:-}" ] && CONFIG[GROQ_API_KEY]="${value}" && \
                            log_ok "    Found Groq key (as OPENAI_API_KEY) in ${envfile}"
                    fi
                    ;;
                TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN|telegram_bot_token)
                    [ -z "${CONFIG[TELEGRAM_BOT_TOKEN]:-}" ] && CONFIG[TELEGRAM_BOT_TOKEN]="${value}" && \
                        log_ok "    Found TELEGRAM_BOT_TOKEN in ${envfile}"
                    ;;
                TELEGRAM_CHAT_ID|telegram_chat_id)
                    [ -z "${CONFIG[TELEGRAM_CHAT_ID]:-}" ] && CONFIG[TELEGRAM_CHAT_ID]="${value}" && \
                        log_ok "    Found TELEGRAM_CHAT_ID in ${envfile}"
                    ;;
                PORTAINER_API_KEY|portainer_api_key|PORTAINER_TOKEN)
                    [ -z "${CONFIG[PORTAINER_API_KEY]:-}" ] && CONFIG[PORTAINER_API_KEY]="${value}" && \
                        log_ok "    Found PORTAINER_API_KEY in ${envfile}"
                    ;;
                GRAFANA_API_KEY|grafana_api_key|GF_SECURITY_ADMIN_PASSWORD)
                    [ -z "${CONFIG[GRAFANA_API_KEY]:-}" ] && CONFIG[GRAFANA_API_KEY]="${value}" && \
                        log_ok "    Found GRAFANA_API_KEY in ${envfile}"
                    ;;
                N8N_API_KEY|n8n_api_key)
                    [ -z "${CONFIG[N8N_API_KEY]:-}" ] && CONFIG[N8N_API_KEY]="${value}" && \
                        log_ok "    Found N8N_API_KEY in ${envfile}"
                    ;;
                SONARR_API_KEY|sonarr_api_key|SONARR__AUTH__APIKEY)
                    [ -z "${CONFIG[SONARR_API_KEY]:-}" ] && CONFIG[SONARR_API_KEY]="${value}" && \
                        log_ok "    Found SONARR_API_KEY in ${envfile}"
                    ;;
                RADARR_API_KEY|radarr_api_key|RADARR__AUTH__APIKEY)
                    [ -z "${CONFIG[RADARR_API_KEY]:-}" ] && CONFIG[RADARR_API_KEY]="${value}" && \
                        log_ok "    Found RADARR_API_KEY in ${envfile}"
                    ;;
                PROWLARR_API_KEY|prowlarr_api_key|PROWLARR__AUTH__APIKEY)
                    [ -z "${CONFIG[PROWLARR_API_KEY]:-}" ] && CONFIG[PROWLARR_API_KEY]="${value}" && \
                        log_ok "    Found PROWLARR_API_KEY in ${envfile}"
                    ;;
                JELLYFIN_API_KEY|jellyfin_api_key|JELLYFIN_TOKEN)
                    [ -z "${CONFIG[JELLYFIN_API_KEY]:-}" ] && CONFIG[JELLYFIN_API_KEY]="${value}" && \
                        log_ok "    Found JELLYFIN_API_KEY in ${envfile}"
                    ;;
                IMMICH_API_KEY|immich_api_key)
                    [ -z "${CONFIG[IMMICH_API_KEY]:-}" ] && CONFIG[IMMICH_API_KEY]="${value}" && \
                        log_ok "    Found IMMICH_API_KEY in ${envfile}"
                    ;;
                NEXTCLOUD_PASSWORD|nextcloud_password|NEXTCLOUD_ADMIN_PASSWORD)
                    [ -z "${CONFIG[NEXTCLOUD_PASSWORD]:-}" ] && CONFIG[NEXTCLOUD_PASSWORD]="${value}" && \
                        log_ok "    Found NEXTCLOUD_PASSWORD in ${envfile}"
                    ;;
                NEXTCLOUD_USER|NEXTCLOUD_ADMIN_USER)
                    [ -z "${CONFIG[NEXTCLOUD_USER]:-}" ] && CONFIG[NEXTCLOUD_USER]="${value}" && \
                        log_ok "    Found NEXTCLOUD_USER in ${envfile}"
                    ;;
                NPM_EMAIL|npm_email)
                    [ -z "${CONFIG[NPM_EMAIL]:-}" ] && CONFIG[NPM_EMAIL]="${value}" && \
                        log_ok "    Found NPM_EMAIL in ${envfile}"
                    ;;
                NPM_PASSWORD|npm_password|INITIAL_ADMIN_PASSWORD)
                    [ -z "${CONFIG[NPM_PASSWORD]:-}" ] && CONFIG[NPM_PASSWORD]="${value}" && \
                        log_ok "    Found NPM_PASSWORD in ${envfile}"
                    ;;
                PIHOLE_API_TOKEN|pihole_api_token|WEBPASSWORD)
                    [ -z "${CONFIG[PIHOLE_API_TOKEN]:-}" ] && CONFIG[PIHOLE_API_TOKEN]="${value}" && \
                        log_ok "    Found PIHOLE_API_TOKEN in ${envfile}"
                    ;;
                SEARXNG_SECRET|SEARXNG_SECRET_KEY)
                    [ -z "${CONFIG[SEARXNG_SECRET]:-}" ] && CONFIG[SEARXNG_SECRET]="${value}" && \
                        log_ok "    Found SEARXNG_SECRET in ${envfile}"
                    ;;
            esac
        done < "${envfile}"
    done <<< "${env_files}"
}

# -----------------------------------------------------------------------------
# Scan shell environment and profile files
# -----------------------------------------------------------------------------
scan_environment() {
    log_info "Scanning environment variables and shell profiles..."

    # Current environment
    for var in GROQ_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID PORTAINER_API_KEY \
               GRAFANA_API_KEY N8N_API_KEY; do
        local val="${!var:-}"
        if [ -n "${val}" ] && [ -z "${CONFIG[${var}]:-}" ]; then
            CONFIG[${var}]="${val}"
            log_ok "  Found ${var} in environment"
        fi
    done

    # Check profile files for exports
    for profile in ~/.bashrc ~/.bash_profile ~/.profile ~/.zshrc /etc/environment; do
        [ -f "${profile}" ] || continue
        while IFS= read -r line; do
            if [[ "${line}" =~ ^export[[:space:]]+([A-Z_]+)=[\"\']*([^\"\']*)[\"\']* ]]; then
                local key="${BASH_REMATCH[1]}"
                local value="${BASH_REMATCH[2]}"
                case "${key}" in
                    GROQ_API_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|PORTAINER_API_KEY)
                        [ -z "${CONFIG[${key}]:-}" ] && CONFIG[${key}]="${value}" && \
                            log_ok "  Found ${key} in ${profile}"
                        ;;
                esac
            fi
        done < "${profile}"
    done
}

# -----------------------------------------------------------------------------
# Scan Docker container configs for embedded secrets/URLs
# -----------------------------------------------------------------------------
scan_docker_containers() {
    log_info "Scanning Docker container environment variables..."

    local containers
    containers=$(docker ps --format '{{.Names}}' 2>/dev/null || true)

    while IFS= read -r container; do
        [ -z "${container}" ] && continue

        # Get env vars from running container
        local envs
        envs=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" 2>/dev/null || true)

        while IFS='=' read -r key value; do
            [ -z "${key}" ] && continue
            case "${key}" in
                GROQ_API_KEY)
                    [ -z "${CONFIG[GROQ_API_KEY]:-}" ] && CONFIG[GROQ_API_KEY]="${value}" && \
                        log_ok "  Found GROQ_API_KEY in container: ${container}"
                    ;;
                OPENAI_API_KEY)
                    if echo "${value}" | grep -q "^gsk_"; then
                        [ -z "${CONFIG[GROQ_API_KEY]:-}" ] && CONFIG[GROQ_API_KEY]="${value}" && \
                            log_ok "  Found Groq key in container: ${container}"
                    fi
                    ;;
                TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN)
                    [ -z "${CONFIG[TELEGRAM_BOT_TOKEN]:-}" ] && CONFIG[TELEGRAM_BOT_TOKEN]="${value}" && \
                        log_ok "  Found TELEGRAM_BOT_TOKEN in container: ${container}"
                    ;;
                TELEGRAM_CHAT_ID)
                    [ -z "${CONFIG[TELEGRAM_CHAT_ID]:-}" ] && CONFIG[TELEGRAM_CHAT_ID]="${value}" && \
                        log_ok "  Found TELEGRAM_CHAT_ID in container: ${container}"
                    ;;
            esac
        done <<< "${envs}"
    done <<< "${containers}"
}

# -----------------------------------------------------------------------------
# Auto-detect service URLs from running Docker containers
# -----------------------------------------------------------------------------
detect_service_urls() {
    log_info "Detecting service URLs from running containers..."

    local containers
    containers=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null || true)

    while read -r name ports; do
        [ -z "${name}" ] && continue
        local name_lower
        name_lower=$(echo "${name}" | tr '[:upper:]' '[:lower:]')

        # Extract first host port
        local port
        port=$(echo "${ports}" | grep -oP '0\.0\.0\.0:\K\d+' | head -1 || \
               echo "${ports}" | grep -oP ':::\K\d+' | head -1 || echo "")

        [ -z "${port}" ] && continue

        case "${name_lower}" in
            *portainer*)
                [ -z "${CONFIG[PORTAINER_URL]:-}" ] && CONFIG[PORTAINER_URL]="http://localhost:${port}" && \
                    log_ok "  Portainer: http://localhost:${port}"
                ;;
            *grafana*)
                [ -z "${CONFIG[GRAFANA_URL]:-}" ] && CONFIG[GRAFANA_URL]="http://localhost:${port}" && \
                    log_ok "  Grafana: http://localhost:${port}"
                ;;
            *n8n*)
                [ -z "${CONFIG[N8N_URL]:-}" ] && CONFIG[N8N_URL]="http://localhost:${port}" && \
                    log_ok "  n8n: http://localhost:${port}"
                ;;
            *uptime*kuma*|*kuma*)
                [ -z "${CONFIG[UPTIME_KUMA_URL]:-}" ] && CONFIG[UPTIME_KUMA_URL]="http://localhost:${port}" && \
                    log_ok "  Uptime Kuma: http://localhost:${port}"
                ;;
            *searxng*|*searx*)
                [ -z "${CONFIG[SEARXNG_URL]:-}" ] && CONFIG[SEARXNG_URL]="http://localhost:${port}" && \
                    log_ok "  SearXNG: http://localhost:${port}"
                ;;
            *openclaw*)
                [ -z "${CONFIG[OPENCLAW_URL]:-}" ] && CONFIG[OPENCLAW_URL]="http://localhost:${port}" && \
                    log_ok "  OpenClaw: http://localhost:${port}"
                ;;
            *pihole*)
                [ -z "${CONFIG[PIHOLE_URL]:-}" ] && CONFIG[PIHOLE_URL]="http://localhost:${port}" && \
                    log_ok "  Pi-hole: http://localhost:${port}"
                ;;
            *jellyfin*)
                [ -z "${CONFIG[JELLYFIN_URL]:-}" ] && CONFIG[JELLYFIN_URL]="http://localhost:${port}" && \
                    log_ok "  Jellyfin: http://localhost:${port}"
                ;;
            *immich*)
                [ -z "${CONFIG[IMMICH_URL]:-}" ] && CONFIG[IMMICH_URL]="http://localhost:${port}" && \
                    log_ok "  Immich: http://localhost:${port}"
                ;;
            *nextcloud*)
                [ -z "${CONFIG[NEXTCLOUD_URL]:-}" ] && CONFIG[NEXTCLOUD_URL]="http://localhost:${port}" && \
                    log_ok "  Nextcloud: http://localhost:${port}"
                ;;
        esac
    done <<< "${containers}"
}

# -----------------------------------------------------------------------------
# Check for existing llama.cpp / ik_llama.cpp server configs
# -----------------------------------------------------------------------------
detect_llm_servers() {
    log_info "Detecting LLM server configurations..."

    # Check if servers are already running
    if curl -sf http://localhost:8080/health &>/dev/null; then
        CONFIG[LLM_PRIMARY_URL]="http://localhost:8080"
        log_ok "  Primary LLM server detected on port 8080"
    fi
    if curl -sf http://localhost:8081/health &>/dev/null; then
        CONFIG[LLM_FAST_URL]="http://localhost:8081"
        log_ok "  Fast LLM server detected on port 8081"
    fi

    # Check existing systemd service files for model paths
    for svc in /etc/systemd/system/llama-*.service; do
        [ -f "${svc}" ] || continue
        local model_path
        model_path=$(grep -oP '(?<=--model )\S+' "${svc}" 2>/dev/null || true)
        if [ -n "${model_path}" ] && [ -f "${model_path}" ]; then
            log_ok "  Found model in service $(basename ${svc}): ${model_path}"
        fi
    done
}

# =============================================================================
# Generate .env file
# =============================================================================
generate_env() {
    log_header "Generating Environment Config"

    # If .env already exists, merge — don't overwrite
    if [ -f "${ENV_FILE}" ]; then
        log_info "Existing .env found. Merging discovered values (won't overwrite existing)..."

        # Read existing values
        while IFS='=' read -r key value; do
            [[ "${key}" =~ ^#.*$ ]] && continue
            [ -z "${key}" ] && continue
            value=$(echo "${value}" | sed 's/^["'\''"]//; s/["'\''"]$//' | xargs)
            # Existing values take precedence
            if [ -n "${value}" ]; then
                CONFIG[${key}]="${value}"
            fi
        done < "${ENV_FILE}"
    fi

    # Write the final .env
    cat > "${ENV_FILE}" << 'HEADER'
# =============================================================================
# AgentHarness Environment Configuration
# Auto-discovered + merged from existing system configs
# Edit only values marked MANUAL below
# =============================================================================

HEADER

    # Write discovered values with source comments
    local missing=()

    write_var() {
        local key="$1"
        local desc="$2"
        local required="${3:-false}"
        local val="${CONFIG[${key}]:-}"

        echo "# --- ${desc} ---" >> "${ENV_FILE}"
        if [ -n "${val}" ]; then
            echo "${key}=${val}" >> "${ENV_FILE}"
        else
            echo "# ${key}=  # MANUAL: fill in this value" >> "${ENV_FILE}"
            [ "${required}" = "true" ] && missing+=("${key}")
        fi
        echo "" >> "${ENV_FILE}"
    }

    write_var "GROQ_API_KEY" "Groq API (emergency escalation)" "false"
    echo "GROQ_DAILY_LIMIT=200" >> "${ENV_FILE}"
    echo "" >> "${ENV_FILE}"

    write_var "TELEGRAM_BOT_TOKEN" "Telegram Bot (Chaguli notifications)" "false"
    write_var "TELEGRAM_CHAT_ID" "Telegram Chat ID" "false"

    write_var "PORTAINER_URL" "Portainer" "false"
    write_var "PORTAINER_API_KEY" "Portainer API Key" "false"

    write_var "LLM_PRIMARY_URL" "Local LLM - Primary (auto-detected)" "false"
    write_var "LLM_FAST_URL" "Local LLM - Fast (auto-detected)" "false"

    write_var "SEARXNG_URL" "SearXNG (auto-detected)" "false"
    write_var "OPENCLAW_WORKSPACE" "OpenClaw workspace path" "false"

    # Network schedule
    cat >> "${ENV_FILE}" << 'SCHEDULE'
# --- Network Schedule (PT timezone) ---
OFFLINE_START_HOUR=23
ONLINE_START_HOUR=7
TIMEZONE=America/Los_Angeles

# --- Mini PC (set when it arrives) ---
# MINIPC_IP=192.168.x.x
# MINIPC_SSH_USER=rohit

# --- GitHub Auto-Deploy ---
DEPLOY_DIR=/opt/deployments

SCHEDULE

    # Write any additional discovered service URLs
    for key in GRAFANA_URL GRAFANA_API_KEY N8N_URL N8N_API_KEY \
               UPTIME_KUMA_URL PIHOLE_URL JELLYFIN_URL IMMICH_URL NEXTCLOUD_URL; do
        local val="${CONFIG[${key}]:-}"
        if [ -n "${val}" ]; then
            echo "${key}=${val}" >> "${ENV_FILE}"
        fi
    done

    echo "" >> "${ENV_FILE}"

    # Summary
    local found_count=0
    local total_count=0
    for key in "${!CONFIG[@]}"; do
        ((total_count++))
        [ -n "${CONFIG[${key}]}" ] && ((found_count++))
    done

    log_ok "Environment file: ${ENV_FILE}"
    log_info "Auto-discovered: ${found_count} values"

    if [ ${#missing[@]} -gt 0 ]; then
        log_warn "Missing (optional, fill in manually):"
        for m in "${missing[@]}"; do
            echo "  - ${m}"
        done
    fi
}

# =============================================================================
# Save discovery results as JSON (for other scripts)
# =============================================================================
save_discovery() {
    python3 -c "
import json
config = {}
$(for key in "${!CONFIG[@]}"; do
    echo "config['${key}'] = '${CONFIG[${key}]}'"
done)
json.dump(config, open('${DISCOVERED}', 'w'), indent=2)
print(f'Saved {len(config)} discovered values to ${DISCOVERED}')
" 2>/dev/null || true
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "Config Discovery"

    ensure_dir /opt/agentharness

    scan_env_files
    scan_environment
    scan_docker_containers
    detect_service_urls
    detect_llm_servers

    save_discovery
    generate_env

    log_header "Discovery Complete"
    echo ""
    echo "  Review: ${ENV_FILE}"
    echo "  Raw discovery: ${DISCOVERED}"
    echo ""
}

main "$@"
