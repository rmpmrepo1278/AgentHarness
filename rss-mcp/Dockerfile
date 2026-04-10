FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY . .
RUN mkdir -p /data
EXPOSE 8104
CMD ["python3", "server.py"]
