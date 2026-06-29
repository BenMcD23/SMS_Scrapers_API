FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      wget gnupg ca-certificates fonts-liberation \
      libnss3 libgdk-pixbuf-xlib-2.0-0 libasound2 libx11-xcb1 \
      libxcomposite1 libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxtst6 \
      libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libpango-1.0-0 libpangocairo-1.0-0 \
      libxrandr2 libxkbcommon0 \
      poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# postgresql-client-16 (pg_dump/psql) for DB backups & restores. Bookworm ships
# v15, which can't dump a v16 server, so pull the matching client from PGDG.
RUN install -d /usr/share/postgresql-common/pgdg \
    && wget -qO /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
         https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Browser OS libs are installed manually above; `playwright install-deps` is
# skipped because it pulls obsolete font packages (ttf-unifont/ttf-ubuntu-font-
# family) that no longer exist on Debian bookworm and fail the build.
RUN playwright install chromium

COPY . .

RUN mkdir -p /app/data

EXPOSE 8000

ENV PYTHONPATH=/app/app:/app

CMD ["sh", "-c", "alembic -c database/alembic.ini upgrade head && uvicorn api:app --host 0.0.0.0 --port 8000"]
