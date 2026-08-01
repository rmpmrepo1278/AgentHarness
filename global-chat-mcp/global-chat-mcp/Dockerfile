# Communication Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mcp_base /mcp_base
COPY server.py /app/server.py

WORKDIR /app

ENV MCP_BASE_DIR=/mcp_base \
    PYTHONPATH=/app:/mcp_base \
    PYTHONUNBUFFERED=1

EXPOSE 8104

CMD ["python3", "server.py"]