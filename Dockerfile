# syntax=docker/dockerfile:1.7
FROM python:3.10-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # Browsers install to a shared path so the non-root runtime user can use them.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Chromium's runtime libraries for the Bader scrapers, poppler for PDF
# rendering, and the PostgreSQL 16 client (pg_dump/psql) for backups. The slim
# base ships an older client that can't dump a v16 server, so pull the matching
# one from PGDG; the codename is read from the base image so it tracks
# bookworm/trixie instead of breaking when the base moves.
RUN apt-get update && apt-get install -y --no-install-recommends \
      wget gnupg ca-certificates fonts-liberation \
      libnss3 libgdk-pixbuf-xlib-2.0-0 libasound2 libx11-xcb1 \
      libxcomposite1 libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxtst6 \
      libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libpango-1.0-0 libpangocairo-1.0-0 \
      libxrandr2 libxkbcommon0 \
      poppler-utils \
    && install -d /usr/share/postgresql-common/pgdg \
    && wget -qO /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
         https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code changes don't invalidate this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt \
    # Browser OS libs are installed above; `playwright install-deps` is skipped
    # because it pulls font packages that no longer exist on bookworm.
    && playwright install chromium \
    && chmod -R a+rX /ms-playwright

# Run as an unprivileged user. Chromium is launched with --no-sandbox because
# the container itself is the sandbox, and this user owns nothing it can't
# afford to lose.
RUN useradd --system --create-home --uid 10001 app
COPY --chown=app:app . .
USER app

ENV PYTHONPATH=/app/app
EXPOSE 8000

# Migrations are NOT run here: the deploy runs them as a separate step, so a
# failed migration never takes the API down.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
