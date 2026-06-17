FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
# Install docker CLI (static binary) and systemd for host management
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl systemd && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz -o /tmp/docker.tgz && \
    tar xzf /tmp/docker.tgz --strip-components=1 -C /usr/local/bin docker/docker && \
    rm /tmp/docker.tgz && \
    apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY . .
EXPOSE 8108
CMD ["python3", "server.py"]
