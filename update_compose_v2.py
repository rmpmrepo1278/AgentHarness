import re

with open('docker-compose.mcp.yml', 'r') as f:
    content = f.read()

# 1. Add mnemo-postgres if missing
if 'mnemo-postgres' not in content:
    postgres = """  mnemo-postgres:
    image: pgvector/pgvector:pg16
    container_name: mnemo-postgres
    network_mode: host
    environment:
      - POSTGRES_DB=mnemo
      - POSTGRES_PASSWORD=hermes_memory_secret
    volumes:
      - /home/rohit/.hermes/data/pg_data:/var/lib/postgresql/data
    restart: unless-stopped\n\n"""
    content = re.sub(r'services:', 'services:\n' + postgres, content)

# 2. Add mnemo-server if missing
if 'mnemo-server' not in content:
    server = """  mnemo-server:
    image: mnemo-server:latest
    container_name: mnemo-server
    network_mode: host
    environment:
      - DATABASE_URL=postgresql://postgres:hermes_memory_secret@127.0.0.1:5432/mnemo
      - PORT=8001
      - HF_HOME=/app/hf_cache
    volumes:
      - ./hf_cache:/app/hf_cache
      - mnemo-data:/app/data
    restart: unless-stopped
    depends_on:
      - mnemo-postgres\n\n"""
    content += server

# 3. Add global-chat-mcp if missing
if 'global-chat-mcp' not in content:
    global_chat = """  global-chat-mcp:
    build: ./global-chat-mcp
    container_name: global-chat-mcp
    network_mode: host
    environment:
      - MCP_PORT=8106
      - GATEWAY_URL=http://127.0.0.1:8090
      - MCP_BASE_DIR=/mcp-base
    volumes:
      - ./mcp-gateway/mcp_base.py:/mcp-base/mcp_base.py:ro
    restart: unless-stopped
    depends_on:
      - mcp-gateway\n\n"""
    content += global_chat

# 4. Update mnemo-mcp to use mnemo-server
content = re.sub(r'mnemo-mcp:.*?depends_on:.*? - mcp-gateway', 
                 r'mnemo-mcp:\n    build: ./mnemo-mcp\n    container_name: mnemo-mcp\n    network_mode: host\n    volumes:\n      - mnemo-data:/app/data\n      - ./hf_cache:/app/hf_cache\n    environment:\n      - PUBLIC_URL=http://0.0.0.0:8096\n      - MCP_PORT=8096\n      - MNEMO_SERVER_URL=http://127.0.0.1:8001\n      - GATEWAY_URL=http://127.0.0.1:8090\n    healthcheck:\n      test: [\"CMD\", \"python3\", \"-c\", \"import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8096/health\', timeout=3) or urllib.request.urlopen(\'http://127.0.0.1:8096/\', timeout=3)\"]\n      interval: 30s\n      timeout: 10s\n      retries: 3\n      start_period: 60s\n    restart: unless-stopped\n    depends_on:\n      - mnemo-server',
                 content, flags=re.DOTALL)

with open('docker-compose.mcp.yml', 'w') as f:
    f.write(content)
