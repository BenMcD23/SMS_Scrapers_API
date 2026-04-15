# SMS Scrapers API

FastAPI backend for the 317 SMS site - handles scrapers, assessments, stores, and cadet management.

## Prerequisites

- Docker & Docker Compose
- `poppler-utils` for PDF processing: `sudo apt install poppler-utils`

## Running

```bash
# Start the database
docker compose up db -d

# Start all services
docker compose up -d

# Expose via Tailscale funnel
docker exec -d tailscale tailscale funnel http://172.18.0.2:8000

# Stop all services
docker compose down
```

### Local dev (without Docker)

```bash
PYTHONPATH=app:. uvicorn api:app --reload
```

## Database Migrations (Alembic)

```bash
# Generate a new migration
alembic -c database/alembic.ini revision --autogenerate -m "<description>"

# Apply migrations
alembic -c database/alembic.ini upgrade head
```
