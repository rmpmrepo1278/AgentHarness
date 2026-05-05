FROM python:3.11-slim

WORKDIR /app

# Install Node.js for npx
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# Install dependencies for the bridge
RUN pip install --no-cache-dir requests

# Copy the bridge script
COPY server.py .

# Assume mcp_base.py is mounted from the gateway
ENV MCP_BASE_DIR=/mcp-base

CMD ["python", "server.py"]
