FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests crawl4ai && crawl4ai-setup
COPY . .
EXPOSE 8125
CMD ["python3", "server.py"]
