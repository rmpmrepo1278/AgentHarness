FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "graphifyy[mcp]" requests

WORKDIR /app
COPY server.py .

ENV PYTHONUNBUFFERED=1
ENV GRAPHIFY_GRAPH_PATH=/data/graphify-out/graph.json

CMD ["python", "server.py"]
