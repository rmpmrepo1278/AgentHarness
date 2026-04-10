FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY . .
EXPOSE 8098
CMD ["python3", "server.py"]
