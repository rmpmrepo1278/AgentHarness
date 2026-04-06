#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# setup_minipc.sh — Playbook for when the Ryzen 8745HS mini PC arrives
#
# Run this on the HP laptop AFTER connecting the mini PC via ethernet.
# It discovers the mini PC, tests connectivity, and sets up the two-machine
# architecture.
#
# Prerequisites:
#   - Mini PC running Debian with SSH enabled
#   - Ethernet cable between HP laptop and mini PC
#   - Static IPs or DHCP reservation on the ethernet subnet
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

main() {
    log_header "Mini PC Setup Playbook"

    echo "  This script will guide you through setting up the two-machine architecture."
    echo "  Run this on the HP laptop after connecting the mini PC via ethernet."
    echo ""

    # Step 1: Discover the mini PC
    log_header "Step 1: Discover Mini PC"

    local minipc_ip="${MINIPC_IP:-}"

    if [ -z "${minipc_ip}" ]; then
        log_info "Scanning local network for new devices..."

        # Try common subnet ranges
        local found_ip=""
        for subnet in 192.168.1 192.168.0 10.0.0 172.16.0; do
            log_info "Scanning ${subnet}.0/24..."
            for i in $(seq 1 254); do
                if ping -c 1 -W 1 "${subnet}.${i}" &>/dev/null; then
                    local known=false
                    # Check if this is our own IP
                    ip addr show 2>/dev/null | grep -q "${subnet}.${i}" && known=true
                    if [ "${known}" = false ]; then
                        echo "  Found: ${subnet}.${i}"
                        found_ip="${subnet}.${i}"
                    fi
                fi
            done &
        done
        wait

        if [ -n "${found_ip}" ]; then
            log_info "Potential mini PC IPs found above."
            echo ""
            echo "  Enter the mini PC's IP address (or press Enter to skip):"
            read -rp "  Mini PC IP: " minipc_ip
        fi
    fi

    if [ -z "${minipc_ip}" ]; then
        log_warn "No mini PC IP configured. Set MINIPC_IP in /opt/agentharness/.env"
        log_info "Once you know the IP, update .env and re-run this script."
        return 1
    fi

    # Test connectivity
    if ping -c 3 -W 2 "${minipc_ip}" &>/dev/null; then
        log_ok "Mini PC reachable at ${minipc_ip}"
    else
        log_error "Cannot reach ${minipc_ip}. Check ethernet connection."
        return 1
    fi

    # Save to .env
    if ! grep -q "MINIPC_IP" /opt/agentharness/.env 2>/dev/null; then
        echo "" >> /opt/agentharness/.env
        echo "# --- Mini PC ---" >> /opt/agentharness/.env
        echo "MINIPC_IP=${minipc_ip}" >> /opt/agentharness/.env
        log_ok "Saved MINIPC_IP=${minipc_ip} to .env"
    fi

    # Step 2: Test SSH
    log_header "Step 2: SSH Connectivity"

    local ssh_user="${MINIPC_SSH_USER:-$(whoami)}"
    log_info "Testing SSH to ${ssh_user}@${minipc_ip}..."

    if ssh -o ConnectTimeout=5 -o BatchMode=yes "${ssh_user}@${minipc_ip}" "echo OK" 2>/dev/null; then
        log_ok "SSH works (key-based auth)"
    else
        log_warn "SSH key auth failed. You may need to:"
        echo "  1. Copy your SSH key: ssh-copy-id ${ssh_user}@${minipc_ip}"
        echo "  2. Or set MINIPC_SSH_USER in .env"
        echo ""
        echo "  After setting up SSH, re-run this script."
    fi

    # Step 3: Discover mini PC specs
    log_header "Step 3: Mini PC Hardware"

    local remote_info
    remote_info=$(ssh -o ConnectTimeout=5 "${ssh_user}@${minipc_ip}" "
        echo CPU: \$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)
        echo RAM: \$(awk '/MemTotal/ {printf \"%.0f GB\", \$2/1024/1024}' /proc/meminfo)
        echo Disk: \$(df -h / | awk 'NR==2 {print \$2 \" total, \" \$4 \" free\"}')
        echo GPU: \$(lspci 2>/dev/null | grep -i vga | cut -d: -f3 | xargs || echo 'unknown')
        echo Docker: \$(docker --version 2>/dev/null || echo 'not installed')
    " 2>/dev/null || echo "Could not retrieve specs (SSH may need setup)")

    echo "${remote_info}"

    # Step 4: Recommendations
    log_header "Step 4: Recommended Architecture"

    echo "  Based on the two-machine setup:"
    echo ""
    echo "  HP LAPTOP (Ryzen 4700U, 36GB RAM):"
    echo "    - Primary LLM server (35B model — needs RAM)"
    echo "    - AgentHarness scheduler + monitoring"
    echo "    - OpenClaw Gateway + Chaguli"
    echo "    - Pi-hole, NPM, Homarr (lightweight services)"
    echo ""
    echo "  MINI PC (Ryzen 8745HS, 16GB RAM, 780M iGPU):"
    echo "    - Fast LLM server (9B model + Vulkan iGPU acceleration)"
    echo "    - Jellyfin (transcoding on iGPU)"
    echo "    - Immich (ML tasks on iGPU)"
    echo "    - Nextcloud, arr stack (storage-heavy)"
    echo ""
    echo "  SHARED (ethernet, always connected):"
    echo "    - Distributed inference (exo) for large models"
    echo "    - Cross-machine monitoring"
    echo "    - Backup replication"
    echo ""

    # Step 5: Next steps checklist
    log_header "Next Steps"

    echo "  [ ] Set up SSH key auth to mini PC"
    echo "  [ ] Install Docker on mini PC"
    echo "  [ ] Install ik_llama.cpp on mini PC (build with -DGGML_VULKAN=ON)"
    echo "  [ ] Migrate heavy services to mini PC"
    echo "  [ ] Configure distributed inference (exo)"
    echo "  [ ] Update AgentHarness scheduler for two-machine mode"
    echo "  [ ] Set up backup replication between machines"
    echo ""
    echo "  Run this script again after completing each step — it will verify progress."
}

main "$@"
