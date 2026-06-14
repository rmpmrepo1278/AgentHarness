FROM node:20-slim

RUN apt-get update -qq && apt-get install -y -qq git build-essential python3 curl 2>/dev/null \
    && git clone --depth 1 https://github.com/0xranx/OpenContext.git /app/opencontext 2>/dev/null \
    && cd /app/opencontext && npm install --ignore-scripts 2>/dev/null \
    && npm rebuild better-sqlite3 2>/dev/null \
    && apt-get remove -y git build-essential 2>/dev/null \
    && apt-get autoremove -y 2>/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY serve.js /app/serve.js

WORKDIR /app/opencontext
EXPOSE 3284
HEALTHCHECK --interval=60s --timeout=10s --retries=3 CMD curl -f http://localhost:3284/health || exit 1

CMD ["node", "/app/serve.js"]
