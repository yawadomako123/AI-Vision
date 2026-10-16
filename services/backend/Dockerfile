# API + built dashboard in one image. Build context: repository root.
#   docker compose --profile full build api

FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /usr/local/bin/uv
WORKDIR /app

COPY services/backend/pyproject.toml services/backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/backend/app ./app
COPY services/backend/migrations ./migrations
COPY services/backend/alembic.ini ./
COPY --from=web /web/dist /app/web

ENV PATH="/app/.venv/bin:$PATH" WEB_DIST=/app/web EVENT_STORAGE_PATH=/data/events
RUN useradd --uid 10001 --create-home app && mkdir -p /data/events && chown -R app /data
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --timeout-keep-alive 30"]
