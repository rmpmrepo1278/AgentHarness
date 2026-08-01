FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    nodejs \
    npm \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @global-chat/mcp-server 2>&1 | tail -3

COPY mcp_base /mcp-base

RUN pip install --no-cache-dir requests

WORKDIR /app

ENV MCP_BASE_DIR=/mcp-base \
    PYTHONPATH=/app:/mcp-base \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

COPY . .

CMD ["python3", "server.py"]
