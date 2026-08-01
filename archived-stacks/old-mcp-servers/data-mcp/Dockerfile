# Data Management Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends     bash     curl     git     jq     python3-pip     && rm -rf /var/lib/apt/lists/*

# Copy mcp_base FIRST (before pip install)
COPY mcp_base /mcp_base

# Install Python dependencies (mcp-base is now available)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY data_management /app/data_management

WORKDIR /app

ENV MCP_BASE_DIR=/mcp_base     PYTHONPATH=/app:/mcp_base     PYTHONUNBUFFERED=1

EXPOSE 8100 8099 8104

CMD [python3, data_management/__init__.py]
