FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_BASE_DIR=/mcp-base
ENV PYTHONUNBUFFERED=1

CMD ["python3", "server.py"]
