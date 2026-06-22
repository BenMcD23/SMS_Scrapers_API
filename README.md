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

## Seeding a test cadet

To test the cadet portal locally, insert a fake cadet row whose email matches your Google account (`ci.mcdonald@317atc.co.uk`).

**Local dev** (exec into the local db container — no `psql` install needed):

```bash
docker exec sms_scrapers_api-db-1 psql -U sms_user -d 317_SMS -c "
INSERT INTO \"Cadets\" (cin, first_name, last_name, email, rank, flight, banned)
VALUES (9999999999, 'Ben', 'McDonald', 'ci.mcdonald@317atc.co.uk', 'Cadet', 'A', false)
ON CONFLICT (cin) DO NOTHING;
"
```

**Dev Docker stack** (running on the server):

```bash
docker exec sms-dev-db-1 psql -U sms_user -d 317_SMS -c "
INSERT INTO \"Cadets\" (cin, first_name, last_name, email, rank, flight, banned)
VALUES (9999999999, 'Ben', 'McDonald', 'ci.mcdonald@317atc.co.uk', 'Cadet', 'A', false)
ON CONFLICT (cin) DO NOTHING;
"
```

The `ON CONFLICT DO NOTHING` makes it safe to re-run. To remove the test cadet afterwards:

```bash
# local
docker exec sms_scrapers_api-db-1 psql -U sms_user -d 317_SMS -c "DELETE FROM \"Cadets\" WHERE cin = 9999999999;"

# dev Docker stack
docker exec sms-dev-db-1 psql -U sms_user -d 317_SMS -c "DELETE FROM \"Cadets\" WHERE cin = 9999999999;"
```

## Database Migrations (Alembic)

**Alembic is the single source of truth for the schema.** The app no longer
calls `Base.metadata.create_all()` on startup — the deployed containers run
`alembic upgrade head` automatically before launching uvicorn (see the `command`
in `docker-compose.yml` / the Dockerfile `CMD`). So on every deploy the schema
is brought up to date from the migration history, and nothing creates tables
out-of-band.

### Adding a schema change

1. Edit the SQLAlchemy models in `database/models.py`.
2. Autogenerate a migration:

   ```bash
   alembic -c database/alembic.ini revision --autogenerate -m "<description>"
   ```

3. **Review the generated file** in `database/alembic/versions/` — autogenerate
   can miss or mis-order things. Check `down_revision` points at the current head.
4. Apply it locally to test:

   ```bash
   alembic -c database/alembic.ini upgrade head
   ```

5. Commit the model change **and** the migration file together. Deploying the
   branch applies it automatically.

### Useful commands

```bash
alembic -c database/alembic.ini current     # what revision the DB is on
alembic -c database/alembic.ini history      # full migration graph
alembic -c database/alembic.ini downgrade -1 # roll back one revision
```

### Fixing "relation already exists" / DuplicateTable

This means the table physically exists but Alembic's `alembic_version` table
still points at an older revision (e.g. a table was created out-of-band by an old
`create_all` startup). The tables are correct — Alembic just needs to be told the
migration is already applied, without re-running its `CREATE TABLE`:

```bash
alembic -c database/alembic.ini stamp head
```

Only use `stamp` when the existing table actually matches the migration. If it
doesn't, drop the stray table first, then `upgrade head` to create it properly.
