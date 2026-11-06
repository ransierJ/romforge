# syntax=docker/dockerfile:1

# ---- Stage 1: build the React frontend ----
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
# vite.config.js writes the build to /build/backend/static
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/backend/static ./static

ENV ROMFORGE_ROMS_ROOT=/roms \
    ROMFORGE_OUTPUT_ROOT=/output \
    ROMFORGE_CONFIG_DIR=/config \
    ROMFORGE_PORT=8096 \
    PYTHONUNBUFFERED=1

EXPOSE 8096
VOLUME ["/roms", "/output", "/config"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8096"]
