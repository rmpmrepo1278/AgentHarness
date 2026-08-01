FROM agentharness-mcp-core:latest
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client systemd && rm -rf /var/lib/apt/lists/*
COPY . .
CMD ["python3", "server.py"]
