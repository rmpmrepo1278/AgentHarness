FROM mcr.microsoft.com/playwright/python:v1.51.0-noble
WORKDIR /app

# The playwright image has Python 3.12 and Chromium pre-installed
# We just need browser-use and langchain-openai
RUN pip install --no-cache-dir browser-use langchain-openai

COPY . .
RUN mkdir -p /data

EXPOSE 8107
CMD ["python3", "server.py"]
