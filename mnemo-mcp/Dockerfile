FROM python:3.13-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y curl jq && rm -rf /var/lib/apt/lists/*

# Install mnemo-mcp server package
RUN pip install --no-cache-dir mnemo-mcp

# HTTP mode for MCP gateway integration
ENV PUBLIC_URL=http://0.0.0.0:8096
ENV MCP_PORT=8096
ENV DB_PATH=/app/data/memories.db

VOLUME ["/app/data"]

# Create entrypoint that configures and starts the server
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
