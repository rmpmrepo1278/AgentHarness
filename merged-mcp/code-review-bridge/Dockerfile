FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests code-review-graph
WORKDIR /app
COPY server.py .
ENV PYTHONUNBUFFERED=1
ENV PATH="/home/rohit/.local/bin:/home/rohit/.bun/bin:/home/rohit/bin:/usr/local/bin:/usr/bin:/bin:/usr/games"
CMD ["python", "server.py"]
