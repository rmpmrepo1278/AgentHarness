#!/bin/sh
# Configure mnemo-mcp
python -c "from mcp_core.storage.config_file import write_config; write_config('mnemo-mcp', {'_setup_complete': True})"

# Start the server in background
python -m mnemo_mcp &

# Wait for server to be ready and register with gateway
echo "Waiting for Mnemo MCP to be ready on :8096..."
for i in $(seq 1 30); do
  if curl -s http://127.0.0.1:8096/ > /dev/null; then
    echo "Mnemo MCP is ready! Registering with gateway..."
    # Fetch tools first
    TOOLS=$(curl -s -X POST http://127.0.0.1:8096/ -H "Content-Type: application/json" -d '{"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}' | jq '.result.tools')
    if [ "$TOOLS" != "null" ] && [ -n "$TOOLS" ]; then
        curl -X POST http://127.0.0.1:8090/register \
             -H "Content-Type: application/json" \
             -d "{\"name\": \"mnemo\", \"address\": \"http://127.0.0.1:8096\", \"tools\": $TOOLS}"
        echo "Registered with gateway!"
        break
    fi
  fi
  sleep 2
done

# Keep container alive
wait
