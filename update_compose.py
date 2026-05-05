import sys
import re

with open('docker-compose.mcp.yml', 'r') as f:
    content = f.read()

# Add mnemo-postgres if not present
if 'mnemo-postgres' not in content:
    postgres_service = """  mnemo-postgres:
    image: pgvector/pgvector:pg16
    container_name: mnemo-postgres
    network_mode: host
    environment:
      - POSTGRES_DB=mnemo
      - POSTGRES_PASSWORD=hermes_memory_secret
    volumes:
      - /home/rohit/.hermes/data/pg_data:/var/lib/postgresql/data
    restart: unless-stopped

"""
    content = content.replace('services:', 'services:\n' + postgres_service)

# Add other services
mnemo_mcp_pattern = r'  mnemo-mcp:.*?depends_on:.*? - mnemo-server'
# Wait, I already added mnemo-server in previous step.
# Let me just check the current state.
