#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# discover_storage.sh — Find USB drives, external storage, and backup targets
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

STORAGE_PATHS="${AH_DATA_DIR}/storage_paths.env"

main() {
    log_info "Discovering storage devices..."

    > "${STORAGE_PATHS}"

    # Find all mounted filesystems, skip virtual/system ones
    local usb_drives=()
    local external_drives=()

    # Method 1: lsblk — find USB and external block devices
    while IFS= read -r line; do
        local name mountpoint size type tran
        name=$(echo "${line}" | awk '{print $1}')
        mountpoint=$(echo "${line}" | awk '{print $7}')
        size=$(echo "${line}" | awk '{print $4}')
        type=$(echo "${line}" | awk '{print $6}')
        tran=$(echo "${line}" | awk '{print $8}')  # transport: usb, sata, etc.

        [ -z "${mountpoint}" ] && continue
        [[ "${mountpoint}" == "/" ]] && continue
        [[ "${mountpoint}" == /boot* ]] && continue
        [[ "${mountpoint}" == /snap* ]] && continue

        if [ "${tran}" = "usb" ] || echo "${mountpoint}" | grep -qi "media\|mnt\|usb\|external"; then
            usb_drives+=("${mountpoint}|${size}|${name}")
            log_ok "Found USB/external: ${mountpoint} (${size}, ${name})"
        fi
    done < <(lsblk -o NAME,FSTYPE,LABEL,SIZE,FSUSED,FSAVAIL,MOUNTPOINT,TRAN -P 2>/dev/null | \
        awk -F'"' '{for(i=2;i<=NF;i+=2) printf "%s ", $i; print ""}' 2>/dev/null || \
        lsblk -o NAME,SIZE,MOUNTPOINT,TRAN 2>/dev/null | tail -n +2)

    # Method 2: mount — find anything under /media or /mnt
    mount 2>/dev/null | while read -r line; do
        local dev mp
        dev=$(echo "${line}" | awk '{print $1}')
        mp=$(echo "${line}" | awk '{print $3}')

        [[ "${mp}" != /media/* ]] && [[ "${mp}" != /mnt/* ]] && continue
        [[ "${dev}" == tmpfs ]] && continue

        # Check if already found
        local already=false
        for u in "${usb_drives[@]:-}"; do
            [[ "${u}" == "${mp}|"* ]] && already=true && break
        done

        if [ "${already}" = false ]; then
            local size
            size=$(df -h "${mp}" 2>/dev/null | awk 'NR==2 {print $2}')
            local avail
            avail=$(df -h "${mp}" 2>/dev/null | awk 'NR==2 {print $4}')
            usb_drives+=("${mp}|${size}|${dev}")
            log_ok "Found mounted: ${mp} (${size}, avail: ${avail})"
        fi
    done

    # Method 3: Look for large drives (>500GB) that might be backup targets
    df -h 2>/dev/null | awk 'NR>1' | while read -r dev size used avail pct mp; do
        [[ "${mp}" == "/" ]] && continue
        [[ "${mp}" == /boot* ]] && continue
        [[ "${mp}" == /snap* ]] && continue
        [[ "${mp}" == /dev* ]] && continue

        # Convert size to GB for comparison
        local size_gb=0
        if [[ "${size}" == *T ]]; then
            size_gb=$(echo "${size}" | sed 's/T//' | awk '{print int($1 * 1024)}')
        elif [[ "${size}" == *G ]]; then
            size_gb=$(echo "${size}" | sed 's/G//' | awk '{print int($1)}')
        fi

        if [ "${size_gb}" -gt 500 ]; then
            log_ok "Large drive: ${mp} (${size}, avail: ${avail})"
        fi
    done

    # Find the best backup target (largest available space)
    local best_backup=""
    local best_avail=0

    for drive_info in "${usb_drives[@]:-}"; do
        [ -z "${drive_info}" ] && continue
        local mp
        mp=$(echo "${drive_info}" | cut -d'|' -f1)
        local avail_kb
        avail_kb=$(df -k "${mp}" 2>/dev/null | awk 'NR==2 {print $4}')
        if [ "${avail_kb:-0}" -gt "${best_avail}" ]; then
            best_avail="${avail_kb}"
            best_backup="${mp}"
        fi
    done

    # Also check for existing backup directories
    local existing_backup_dirs=()
    for candidate in /media/*/backup* /mnt/*/backup* /media/*/Backup* /mnt/*/Backup* \
                     /media/backup* /mnt/backup*; do
        [ -d "${candidate}" ] && existing_backup_dirs+=("${candidate}") && \
            log_ok "Found existing backup dir: ${candidate}"
    done

    # Check for existing backup scripts/cron
    local existing_backup_scripts=()
    if [ -f "${AH_DATA_DIR}/automation_catalog.json" ]; then
        while IFS= read -r path; do
            [ -n "${path}" ] && existing_backup_scripts+=("${path}") && \
                log_info "Found existing backup automation: ${path}"
        done < <(python3 -c "
import json
catalog = json.load(open('${AH_DATA_DIR}/automation_catalog.json'))
for item in catalog.get('items', []):
    if 'backup' in item.get('capabilities', []) or 'backup' in item.get('path', '').lower():
        print(item.get('path', ''))
" 2>/dev/null)
    fi

    # Write results
    echo "# Discovered storage — $(date -Iseconds)" > "${STORAGE_PATHS}"

    if [ -n "${best_backup}" ]; then
        local total
        total=$(df -h "${best_backup}" 2>/dev/null | awk 'NR==2 {print $2}')
        local avail
        avail=$(df -h "${best_backup}" 2>/dev/null | awk 'NR==2 {print $4}')
        echo "BACKUP_DRIVE=${best_backup}" >> "${STORAGE_PATHS}"
        echo "BACKUP_DRIVE_TOTAL=${total}" >> "${STORAGE_PATHS}"
        echo "BACKUP_DRIVE_AVAIL=${avail}" >> "${STORAGE_PATHS}"
        log_ok "Best backup target: ${best_backup} (${total} total, ${avail} free)"
    fi

    if [ ${#existing_backup_dirs[@]} -gt 0 ]; then
        echo "EXISTING_BACKUP_DIRS=\"${existing_backup_dirs[*]}\"" >> "${STORAGE_PATHS}"
    fi

    if [ ${#existing_backup_scripts[@]} -gt 0 ]; then
        echo "EXISTING_BACKUP_SCRIPTS=\"${existing_backup_scripts[*]}\"" >> "${STORAGE_PATHS}"
    fi

    # List all USB drives
    local idx=0
    for drive_info in "${usb_drives[@]:-}"; do
        [ -z "${drive_info}" ] && continue
        local mp
        mp=$(echo "${drive_info}" | cut -d'|' -f1)
        echo "USB_DRIVE_${idx}=${mp}" >> "${STORAGE_PATHS}"
        ((idx++))
    done
    echo "USB_DRIVE_COUNT=${idx}" >> "${STORAGE_PATHS}"

    log_ok "Storage paths saved to ${STORAGE_PATHS}"
}

main "$@"
