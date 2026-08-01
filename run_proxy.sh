#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/data/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

export AH_DATA_DIR="/home/rohit/agentharness/data"
cd /home/rohit/agentharness
exec python3 -m core.providers.proxy_server --host 0.0.0.0 --port 8080
