#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# cleanup.sh — Periodic cleanup of unused containers, images, volumes,
#              packages, orphan files, and accumulated garbage
#
# Designed for a 256GB SSD — disk is a constrained resource.
# Runs during offline window (no internet needed).
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

CLEANUP_REPORT="${AH_REPORTS_DIR}/cleanup_$(timestamp).md"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"

# Track totals
TOTAL_FREED_KB=0

freed() {
    local kb="$1"
    local desc="$2"
    TOTAL_FREED_KB=$((TOTAL_FREED_KB + kb))
    local human
    human=$(numfmt --to=iec --suffix=B $((kb * 1024)) 2>/dev/null || echo "${kb}KB")
    echo "- Freed ${human}: ${desc}" >> "${CLEANUP_REPORT}"
    log_ok "Freed ${human}: ${desc}"
}

# =============================================================================
# Docker cleanup
# =============================================================================
cleanup_docker() {
    echo "## Docker Cleanup" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    # --- Stopped containers (older than 7 days) ---
    local stopped
    stopped=$(docker ps -a --filter "status=exited" --filter "status=dead" \
        --format "{{.ID}} {{.Names}} {{.Status}}" 2>/dev/null || true)

    if [ -n "${stopped}" ]; then
        local count
        count=$(echo "${stopped}" | wc -l)
        echo "### Stopped Containers (${count})" >> "${CLEANUP_REPORT}"

        echo "${stopped}" | while read -r id name status; do
            # Check how long it's been stopped
            local finished_at
            finished_at=$(docker inspect --format='{{.State.FinishedAt}}' "${id}" 2>/dev/null || echo "")
            echo "- \`${name}\`: ${status} (finished: ${finished_at})" >> "${CLEANUP_REPORT}"
        done

        # Remove containers stopped for more than 7 days
        local old_containers
        old_containers=$(docker ps -a --filter "status=exited" \
            --format "{{.ID}} {{.Names}}" 2>/dev/null | while read -r id name; do
            local finished
            finished=$(docker inspect --format='{{.State.FinishedAt}}' "${id}" 2>/dev/null || echo "")
            if [ -n "${finished}" ]; then
                local finished_ts
                finished_ts=$(date -d "${finished}" +%s 2>/dev/null || echo "0")
                local now_ts
                now_ts=$(date +%s)
                local age_days=$(( (now_ts - finished_ts) / 86400 ))
                if [ "${age_days}" -gt 7 ]; then
                    echo "${id} ${name} ${age_days}d"
                fi
            fi
        done)

        if [ -n "${old_containers}" ]; then
            echo "${old_containers}" | while read -r id name age; do
                local size
                size=$(docker inspect --format='{{.SizeRw}}' "${id}" 2>/dev/null || echo "0")
                local size_kb=$((size / 1024))
                docker rm "${id}" 2>/dev/null && freed "${size_kb}" "removed stopped container: ${name} (${age} old)"
            done
        fi
        echo "" >> "${CLEANUP_REPORT}"
    fi

    # --- Dangling images ---
    local dangling_size
    dangling_size=$(docker images -f "dangling=true" --format "{{.Size}}" 2>/dev/null | \
        awk '{
            val=$1; gsub(/[^0-9.]/, "", val);
            if ($1 ~ /GB/) sum += val*1024*1024;
            else if ($1 ~ /MB/) sum += val*1024;
            else if ($1 ~ /KB/) sum += val;
        } END {printf "%.0f", sum}' || echo "0")

    if [ "${dangling_size}" -gt 0 ]; then
        docker image prune -f 2>/dev/null
        freed "${dangling_size}" "dangling Docker images"
    fi

    # --- Unused images (not referenced by any container) ---
    local unused_images
    unused_images=$(docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}" 2>/dev/null | while read -r image size; do
        # Check if any container (running or stopped) uses this image
        local in_use
        in_use=$(docker ps -a --filter "ancestor=${image}" --format "{{.ID}}" 2>/dev/null | head -1)
        if [ -z "${in_use}" ] && [ "${image}" != "<none>:<none>" ]; then
            echo "${image} ${size}"
        fi
    done)

    if [ -n "${unused_images}" ]; then
        echo "### Unused Images (not referenced by any container)" >> "${CLEANUP_REPORT}"
        echo "${unused_images}" | while read -r image size; do
            echo "- \`${image}\` (${size})" >> "${CLEANUP_REPORT}"
        done
        echo "" >> "${CLEANUP_REPORT}"
        echo "To remove: \`docker rmi <image>\`" >> "${CLEANUP_REPORT}"
        echo "" >> "${CLEANUP_REPORT}"
        # Don't auto-remove — user might want to restart these
    fi

    # --- Orphan volumes ---
    local orphan_volumes
    orphan_volumes=$(docker volume ls -f "dangling=true" --format "{{.Name}}" 2>/dev/null || true)

    if [ -n "${orphan_volumes}" ]; then
        echo "### Orphan Volumes" >> "${CLEANUP_REPORT}"
        local vol_count=0
        echo "${orphan_volumes}" | while read -r vol; do
            local vol_size
            vol_size=$(docker system df -v 2>/dev/null | grep "${vol}" | awk '{print $NF}' || echo "unknown")
            echo "- \`${vol}\` (${vol_size})" >> "${CLEANUP_REPORT}"
            ((vol_count++))
        done
        echo "" >> "${CLEANUP_REPORT}"
        echo "To remove all orphan volumes: \`docker volume prune -f\`" >> "${CLEANUP_REPORT}"
        echo "" >> "${CLEANUP_REPORT}"
    fi

    # --- Docker build cache ---
    local build_cache_size
    build_cache_size=$(docker system df 2>/dev/null | awk '/Build Cache/ {print $4}' || echo "0B")
    if [ "${build_cache_size}" != "0B" ] && [ "${build_cache_size}" != "0" ]; then
        docker builder prune -f --keep-storage 1GB 2>/dev/null || true
        echo "- Pruned Docker build cache (was: ${build_cache_size})" >> "${CLEANUP_REPORT}"
    fi

    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# System package cleanup
# =============================================================================
cleanup_packages() {
    echo "## System Package Cleanup" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    # --- APT cleanup ---
    local apt_cache_before
    apt_cache_before=$(du -sk /var/cache/apt/archives/ 2>/dev/null | cut -f1 || echo "0")

    sudo apt-get autoremove -y -qq 2>/dev/null || true
    sudo apt-get autoclean -y -qq 2>/dev/null || true

    local apt_cache_after
    apt_cache_after=$(du -sk /var/cache/apt/archives/ 2>/dev/null | cut -f1 || echo "0")
    local apt_freed=$((apt_cache_before - apt_cache_after))
    [ "${apt_freed}" -gt 0 ] && freed "${apt_freed}" "APT cache cleanup"

    # --- Old kernels (keep current + 1 previous) ---
    local current_kernel
    current_kernel=$(uname -r)
    local old_kernels
    old_kernels=$(dpkg -l 'linux-image-*' 2>/dev/null | awk '/^ii/ {print $2}' | \
        grep -v "${current_kernel}" | grep -v "generic" | head -5)

    if [ -n "${old_kernels}" ]; then
        echo "### Old Kernels (current: ${current_kernel})" >> "${CLEANUP_REPORT}"
        echo "${old_kernels}" | while read -r pkg; do
            echo "- ${pkg}" >> "${CLEANUP_REPORT}"
        done
        echo "To remove: \`sudo apt purge <kernel-package>\`" >> "${CLEANUP_REPORT}"
        echo "" >> "${CLEANUP_REPORT}"
    fi

    # --- Pip cache ---
    local pip_cache_size
    pip_cache_size=$(pip cache info 2>/dev/null | awk '/Size:/ {print $2}' || echo "0")
    if [ "${pip_cache_size}" != "0" ]; then
        pip cache purge 2>/dev/null || true
        echo "- Cleared pip cache (was: ${pip_cache_size})" >> "${CLEANUP_REPORT}"
    fi

    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# Log and temp file cleanup
# =============================================================================
cleanup_logs() {
    echo "## Log & Temp Cleanup" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    # --- Journal logs (keep 7 days) ---
    local journal_before
    journal_before=$(journalctl --disk-usage 2>/dev/null | grep -oP '[\d.]+[GMK]' || echo "0")
    sudo journalctl --vacuum-time=7d --quiet 2>/dev/null || true
    echo "- Vacuumed journald logs (was: ${journal_before})" >> "${CLEANUP_REPORT}"

    # --- Old AgentHarness reports (keep 30 days of daily, 90 days of weekly) ---
    local old_daily
    old_daily=$(find "${AH_REPORTS_DIR}" -name "daily_*" -mtime +30 2>/dev/null | wc -l)
    local old_weekly
    old_weekly=$(find "${AH_REPORTS_DIR}" -name "weekly_*" -mtime +90 2>/dev/null | wc -l)

    if [ "${old_daily}" -gt 0 ]; then
        find "${AH_REPORTS_DIR}" -name "daily_*" -mtime +30 -delete 2>/dev/null
        echo "- Removed ${old_daily} daily reports older than 30 days" >> "${CLEANUP_REPORT}"
    fi
    if [ "${old_weekly}" -gt 0 ]; then
        find "${AH_REPORTS_DIR}" -name "weekly_*" -mtime +90 -delete 2>/dev/null
        echo "- Removed ${old_weekly} weekly reports older than 90 days" >> "${CLEANUP_REPORT}"
    fi

    # --- /tmp cleanup (files older than 3 days) ---
    local tmp_freed_kb
    tmp_freed_kb=$(find /tmp -maxdepth 2 -type f -mtime +3 -printf '%k\n' 2>/dev/null | \
        awk '{sum+=$1} END {print sum+0}')
    if [ "${tmp_freed_kb}" -gt 1024 ]; then
        find /tmp -maxdepth 2 -type f -mtime +3 -delete 2>/dev/null || true
        freed "${tmp_freed_kb}" "/tmp files older than 3 days"
    fi

    # --- Truncate large log files (>100MB) ---
    find /var/log -name "*.log" -size +100M 2>/dev/null | while read -r logfile; do
        local size_before
        size_before=$(du -sk "${logfile}" | cut -f1)
        sudo truncate -s 10M "${logfile}" 2>/dev/null
        local size_after
        size_after=$(du -sk "${logfile}" | cut -f1)
        freed $((size_before - size_after)) "truncated ${logfile}"
    done

    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# Stale model cleanup
# =============================================================================
cleanup_models() {
    echo "## Model Cleanup" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    # Find GGUF files not in the current catalog
    if [ -f "${AH_DATA_DIR}/model_catalog.json" ]; then
        local cataloged_paths
        cataloged_paths=$(python3 -c "
import json
catalog = json.load(open('${AH_DATA_DIR}/model_catalog.json'))
for m in catalog:
    print(m['gguf_path'])
" 2>/dev/null)

        find /opt/models -name "*.gguf" -type f 2>/dev/null | while read -r gguf; do
            if ! echo "${cataloged_paths}" | grep -qF "${gguf}"; then
                local size
                size=$(du -sh "${gguf}" | cut -f1)
                echo "- Uncataloged model: \`${gguf}\` (${size})" >> "${CLEANUP_REPORT}"
            fi
        done
    fi

    # Find incomplete downloads (small .gguf files that are likely corrupted)
    find /opt/models -name "*.gguf" -size -10M -type f 2>/dev/null | while read -r small_gguf; do
        echo "- Suspicious small GGUF (likely incomplete download): \`${small_gguf}\` ($(du -sh "${small_gguf}" | cut -f1))" >> "${CLEANUP_REPORT}"
    done

    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# Disk usage analysis
# =============================================================================
disk_analysis() {
    echo "## Disk Usage Analysis" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    echo '```' >> "${CLEANUP_REPORT}"
    echo "Total disk:" >> "${CLEANUP_REPORT}"
    df -h / >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"
    echo "Top 15 directories:" >> "${CLEANUP_REPORT}"
    du -sh /opt/* /var/lib/docker /var/log /home/* /tmp 2>/dev/null | sort -rh | head -15 >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"
    echo "Docker disk usage:" >> "${CLEANUP_REPORT}"
    docker system df 2>/dev/null >> "${CLEANUP_REPORT}" || echo "(Docker not available)" >> "${CLEANUP_REPORT}"
    echo '```' >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# LLM-powered analysis (optional, if server is running)
# =============================================================================
llm_analysis() {
    if ! curl -sf "${LLM_URL}/health" &>/dev/null; then
        return 0
    fi

    echo "## AI Cleanup Recommendations" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"

    local context
    context=$(cat "${CLEANUP_REPORT}")

    local analysis
    analysis=$(curl -sf --max-time 300 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
report = open('${CLEANUP_REPORT}').read()
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are a homelab sysadmin. Analyze this cleanup report and suggest the top 3 most impactful cleanup actions the user should take. Be specific about what to remove and how much space it would free. Only suggest safe actions.'},
        {'role': 'user', 'content': report}
    ],
    'max_tokens': 400,
    'temperature': 0.2
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except:
    print('(LLM unavailable)')
" 2>/dev/null || echo "(LLM analysis unavailable)")

    echo "${analysis}" >> "${CLEANUP_REPORT}"
    echo "" >> "${CLEANUP_REPORT}"
}

# =============================================================================
# Notify
# =============================================================================
notify_cleanup() {
    local total_human
    total_human=$(numfmt --to=iec --suffix=B $((TOTAL_FREED_KB * 1024)) 2>/dev/null || echo "${TOTAL_FREED_KB}KB")

    bash "${AH_SCRIPTS_DIR}/alert.sh" INFO "Cleanup complete: freed ${total_human}. See ${CLEANUP_REPORT}" cleanup
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "System Cleanup"

    ensure_dir "${REPORT_DIR}"

    # Initialize report
    cat > "${CLEANUP_REPORT}" << EOF
# AgentHarness Cleanup Report
**Date**: $(date '+%Y-%m-%d %H:%M')
**Disk before**: $(df -h / | awk 'NR==2 {print $3 " used / " $2 " total (" $5 ")"}')

---

EOF

    cleanup_docker
    cleanup_packages
    cleanup_logs
    cleanup_models
    disk_analysis
    llm_analysis

    # Footer
    local total_human
    total_human=$(numfmt --to=iec --suffix=B $((TOTAL_FREED_KB * 1024)) 2>/dev/null || echo "${TOTAL_FREED_KB}KB")

    cat >> "${CLEANUP_REPORT}" << EOF

---
**Total freed: ${total_human}**
**Disk after**: $(df -h / | awk 'NR==2 {print $3 " used / " $2 " total (" $5 ")"}')
EOF

    notify_cleanup

    log_ok "Cleanup complete. Freed: ${total_human}"
    log_ok "Report: ${CLEANUP_REPORT}"
}

main "$@"
