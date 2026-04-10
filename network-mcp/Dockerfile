FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping iproute2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY . .
EXPOSE 8103
CMD ["python3", "server.py"]
