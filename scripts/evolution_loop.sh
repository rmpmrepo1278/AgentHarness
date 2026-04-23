#!/usr/bin/env bash
set -euo pipefail
cd /home/rohit/agentharness
AH_DATA_DIR=/home/rohit/agentharness/data python3 scripts/evolution_loop.py
