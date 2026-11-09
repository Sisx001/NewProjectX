# syntax=docker/dockerfile:1.7
# VOLTORA: one image serving the API and the built web app (FastAPI static fallback in app.main).
# Build from the repository root:  docker build -f deploy/Dockerfile -t voltora:local .
# UNVERIFIED in the authoring environment (no Docker daemon was available); see docs/DEPLOYMENT.md.

FROM node:24-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    PATH=/app/backend/.venv/bin:$PATH \
    VOLTORA_DATA_DIR=/data

# pg_dump/pg_restore 16 (matches the postgres:16 service) for in-app backups and restore drills.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin voltora \
 && mkdir -p /app/backend /app/web /data && chown -R voltora:voltora /app /data

WORKDIR /app/backend
COPY --chown=voltora:voltora backend/pyproject.toml backend/uv.lock ./
USER voltora
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=voltora:voltora backend/ ./
COPY --from=web --chown=voltora:voltora /src/web/dist /app/web/dist
COPY --chown=voltora:voltora deploy/docker-entrypoint.sh /usr/local/bin/voltora-entrypoint
USER root
RUN chmod 0755 /usr/local/bin/voltora-entrypoint
USER voltora

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["voltora-entrypoint"]
# One process: the outbox worker and periodic jobs run in-process (VOLTORA_RUN_WORKER_IN_PROCESS), so do not add --workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
