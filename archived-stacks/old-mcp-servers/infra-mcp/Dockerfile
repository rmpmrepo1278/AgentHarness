# Infrastructure Services Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends     bash     curl     docker.io     git     jq     procps     rsync     sqlite3     tar     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy mcp_base FIRST (needed for requirements)
COPY mcp_base /mcp_base

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy infrastructure services
COPY infrastructure /app/infrastructure

ENV MCP_BASE_DIR=/mcp_base     PYTHONPATH=/app:/mcp_base     PYTHONUNBUFFERED=1

EXPOSE 8102 8105 8097

CMD [python3, infrastructure/server.py]
