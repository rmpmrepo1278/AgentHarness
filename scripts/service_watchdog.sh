#!/usr/bin/env bash
# Deprecated: use health_check.sh (cron every 5 min).
# Kept as a thin wrapper for manual runs and legacy references.
exec /home/rohit/agentharness/scripts/health_check.sh "$@"
