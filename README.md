# SMS Scrapers API

FastAPI backend for the 317 SMS site - handles scrapers, assessments, stores, and cadet management.

## Prerequisites

- Docker & Docker Compose

## Environments

Two stacks run on the server simultaneously:
- **prod** — `main` branch, port 8000, exposed via `tailscale-prod`
- **dev**  — `development` branch, port 8001, exposed via `tailscale-dev`

Deployments are handled automatically by GitHub Actions on push to either branch.

## Server setup (one-time)

```bash
# Prod
mkdir -p ~/sms-api/prod && cd ~/sms-api/prod
git clone https://github.com/BenMcD23/SMS_Scrapers_API.git .
git checkout main
cp .env.tmpl .env  # fill in secrets

# Dev
mkdir -p ~/sms-api/dev && cd ~/sms-api/dev
git clone https://github.com/BenMcD23/SMS_Scrapers_API.git .
git checkout development
cp .env.tmpl .env  # fill in secrets
```

## Starting the stacks

```bash
# Prod
cd ~/sms-api/prod
docker compose -p sms-prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Dev
cd ~/sms-api/dev
docker compose -p sms-dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## Stopping the stacks

```bash
# Prod
cd ~/sms-api/prod && docker compose -p sms-prod down

# Dev
cd ~/sms-api/dev && docker compose -p sms-dev down
```

## Authorising Tailscale (first run)

After starting, the Tailscale containers need to be logged in once:

```bash
docker exec tailscale-prod tailscale up --accept-dns=false
docker exec tailscale-dev  tailscale up --accept-dns=false
```

Open the printed login URLs in a browser. State is persisted in `./tailscale_data` so this only needs to be done once per container.

## Logs

```bash
# All containers
cd ~/sms-api/prod && docker compose -p sms-prod logs -f
cd ~/sms-api/dev  && docker compose -p sms-dev logs -f

# Single container (api, db, tailscale-prod, tailscale-dev)
docker compose -p sms-prod logs -f api
docker compose -p sms-dev  logs -f api
```

## Local dev (without Docker)

You still need a running PostgreSQL instance. Use the local override to publish the port to `localhost:5432`:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db
```

Then set up the Python environment:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the env template and fill in secrets:

```bash
cp .env.tmpl .env
# Edit .env — at minimum set POSTGRES_PASSWORD and any API keys you need
```

Set the database URL to point at the local Docker db and run migrations:

```bash
export DATABASE_URL="postgresql+psycopg2://sms_user:<POSTGRES_PASSWORD>@localhost:5432/317_SMS"
alembic -c database/alembic.ini upgrade head
```

Start the dev server:

```bash
PYTHONPATH=app:. uvicorn api:app --reload
```

The API will be available at `http://localhost:8000`. Interactive docs are at `http://localhost:8000/docs`.

## Database Migrations (Alembic)

```bash
# Generate a new migration
alembic -c database/alembic.ini revision --autogenerate -m "<description>"

# Apply migrations
alembic -c database/alembic.ini upgrade head
```
