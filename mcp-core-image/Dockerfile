# agentharness-mcp-core — unified base for all MCP servers
FROM python:3.12-slim

WORKDIR /app

# Common Python dependencies (merge of all requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Docker CLI (needed by docker-mcp, homelab-exec, backup-mcp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz -o /tmp/docker.tgz && \
    tar xzf /tmp/docker.tgz --strip-components=1 -C /usr/local/bin docker/docker && \
    rm /tmp/docker.tgz && \
    apt-get remove -y curl && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

LABEL maintainer="homelab" description="Unified MCP base — all common deps for agentharness MCP servers"
