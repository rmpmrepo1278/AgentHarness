#!/bin/bash
cd /home/rohit/agentharness
set -a
source data/.env
set +a
exec ./venv/bin/python3 -m core.providers.proxy_server --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data
