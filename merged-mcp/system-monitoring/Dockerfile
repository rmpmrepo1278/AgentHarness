# System Monitoring Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    docker.io \
    iproute2 \
    iputils-ping \
    net-tools \
    netcat-openbsd \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY mcp_base /mcp_base
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY system_monitoring /app/system_monitoring
COPY registry.py router.py health.py gateway_log.py notify.py rate_limiter.py /app/
COPY server.py /app/server.py

WORKDIR /app

ENV MCP_BASE_DIR=/mcp_base \
    PYTHONPATH=/app:/mcp_base \
    PYTHONUNBUFFERED=1

EXPOSE 8103 8106 8090

CMD ["python3", "server.py"]
