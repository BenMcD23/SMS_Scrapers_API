# SMS Scrapers API

FastAPI + SQLAlchemy backend for 317 (Failsworth) Squadron RAFAC. It serves the
staff [SMS site](https://github.com/BenMcD23/317_SMS_Site) and the
[cadet portal](https://github.com/BenMcD23/317_Cadet_Portal), scrapes Bader for
cadet records, and generates the assessment and admin paperwork.

## Prerequisites

- Python 3.14 (what the Docker image and CI use)
- Docker & Docker Compose, only for the full Postgres setup
- `kubectl` and `kubeseal` with the homelab kubeconfig, for anything touching
  the deployed environments

## Running locally

### Preview: seeded data, no setup

```bash
python app/scripts/dev_server.py           # http://localhost:8000, docs at /docs
python app/scripts/dev_server.py --reset   # re-seed from scratch
```

This serves the API from a seeded SQLite file (`data/dev.db`) with fake auth
and email off. It needs no `.env`, Postgres, Google or cluster access, so it
works on a fresh clone and in a cloud session. The seed is about 60 cadets in
flights A–C, staff, attendance, qualifications, stores stock and orders, stored
the way the scrapers store them.

To click around the frontends against it, run `scripts/preview.sh` in the
[SMS site](https://github.com/BenMcD23/317_SMS_Site) repo instead. It starts
this server, the SMS site and the cadet portal together, with STAFF / SNCO /
NCO sign-in buttons. SNCO and NCO are cadets with records, so this is also how
to test the cadet portal.

### Full setup: Postgres and the real dev secrets

Set up the Python environment:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Generate `.env`. There is no template to fill in: config comes from
`deploy/*/config.env`, and secrets are decrypted from the dev environment in the
cluster (needs `kubectl` access). Local settings go on top: the local DB, fake
auth, and email off.

```bash
python deploy/dev-env.py        # re-run whenever a secret or config.env changes
```

Start PostgreSQL (on `localhost:5432`, reading `POSTGRES_PASSWORD` from `.env`), then run migrations:

```bash
docker compose up -d
alembic -c app/database/alembic.ini upgrade head
```

Start the dev server:

```bash
PYTHONPATH=app uvicorn api:app --reload
```

The API will be available at `http://localhost:8000`. Interactive docs are at `http://localhost:8000/docs`.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check app    # lint
pytest
```

`pytest.ini` puts both the repo root and `app/` on `sys.path`, so there is no
`PYTHONPATH` to remember and tests can be run from anywhere in the repo. Useful
variants:

```bash
pytest app/routers/test_nco_holidays.py   # one file
pytest -k inspection                      # by name
pytest -x -vv                             # stop at the first failure, verbose
```

Everything runs against in-memory SQLite with Google calls stubbed, so no
database, network or credentials are needed.

CI (`.github/workflows/tests.yml`) runs lint and tests on every push and pull
request. On pull requests it also builds the Docker image, so a broken
Dockerfile or pin is caught before it can block a deploy. `security.yml` runs
`pip-audit` on pull requests and weekly. It doesn't block merging, but a red
audit should be fixed.

## Environments

Both run on the k3s homelab cluster, deployed by Argo CD from `deploy/`:

| | Branch | Namespace | URL |
|-|--------|-----------|------------------------|
| **prod** | `main` | `sms-prod` | `https://smsapi.317atc.co.uk` |
| **dev** | `development` | `sms-dev` | `https://smsapi-dev.317atc.co.uk` |

A push to either branch runs `.github/workflows/deploy.yml`: build the image,
push it to GHCR with a version tag, and commit that tag into the environment's
overlay. Argo CD sees the commit and deploys it, running Alembic
first (`deploy/base/migrate.yaml`). A failed migration stops the deploy with
the old pod still serving.

```
deploy/base/            API Deployment, Postgres (CloudNativePG), migration Job, Ingress
deploy/overlays/prod/   config, SealedSecret, the switchover CronJob
deploy/overlays/dev/    config, SealedSecret, one DB instance
deploy/dev-env.py       writes a local .env from the above (see Local dev)
```

Placement and failover are explained in the homelab repo's ADR 0007. In
short: prod's database runs as two instances, on squadron and home. The
primary (and the API with it) lives on home; squadron's link is too flaky to
hold it.

### Versions

Images are tagged `v<VERSION>.<commit count>`, for example `v1.0.412`, and dev
builds get `-dev`. `VERSION` holds the major.minor. Bump it by hand for a
release worth naming, and the last number takes care of itself. The deployed
version is the `newTag` in the overlay, and it also shows in Argo CD.

## Config and secrets

There is no `.env` on any server.

- **Plain settings** are in `deploy/base/config.env` (shared), plus
  `deploy/overlays/<env>/config.env` (per environment, overriding the shared
  values). Kustomize builds them into the `sms-api-config` ConfigMap. Edit,
  commit, and push.
- **Secrets** are `deploy/overlays/<env>/sealed-secret.yaml`, encrypted to
  the cluster's Sealed Secrets key. To add or change one:

  ```bash
  echo -n 'the-value' | kubectl create secret generic sms-api-secrets -n sms-prod \
    --dry-run=client --from-file=NEW_KEY=/dev/stdin -o yaml \
    | kubeseal -o yaml --merge-into deploy/overlays/prod/sealed-secret.yaml
  ```

  Then commit and push. The API reads both as environment variables, and a
  config change rolls the pod.
- **`DATABASE_URL`** is written by CloudNativePG (secret `sms-db-app`) and
  never set by hand.

`NEXT_PUBLIC_*` vars like `NEXT_PUBLIC_OC_EMAIL` live with the frontend host
and are baked in at build time. Set them there and redeploy the UI.

## Operating it

```bash
export KUBECONFIG=~/.kube/k3s-homelab.yaml

kubectl -n sms-prod logs deploy/sms-api -f          # API logs
kubectl -n sms-prod get pods -o wide                # what runs where
kubectl -n sms-prod get cluster sms-db              # DB health + current primary
kubectl -n sms-prod logs job/sms-api-migrate        # last migration
kubectl -n sms-prod rollout restart deploy/sms-api  # restart the API
kubectl -n sms-prod top pod                         # memory - tune the 6Gi limit from this

# psql as the app user
kubectl -n sms-prod exec -it deploy/sms-api -- sh -c 'psql "$DATABASE_URL"'
```

Backups work as before: the 03:00 job dumps to Google Drive, and the owner-only
`/backups` endpoints list, preview and restore those dumps.

## Database migrations (Alembic)

**Alembic is the single source of truth for the schema.** The app no longer
calls `Base.metadata.create_all()` on startup. Every deploy runs
`alembic upgrade head` as a Job before the new API pod starts
(`deploy/base/migrate.yaml`), so the schema is brought up to date from the
migration history and nothing creates tables out-of-band. The one exception is a
brand-new empty database, which that Job builds from the models and stamps.

### Adding a schema change

1. Edit the SQLAlchemy models in `database/models.py`.
2. Autogenerate a migration:

   ```bash
   alembic -c app/database/alembic.ini revision --autogenerate -m "<description>"
   ```

3. **Review the generated file** in `database/alembic/versions/` — autogenerate
   can miss or mis-order things. Check `down_revision` points at the current head.
4. Apply it locally to test:

   ```bash
   alembic -c app/database/alembic.ini upgrade head
   ```

5. Commit the model change **and** the migration file together. Deploying the
   branch applies it automatically.

### Useful commands

```bash
alembic -c app/database/alembic.ini current     # what revision the DB is on
alembic -c app/database/alembic.ini history      # full migration graph
alembic -c app/database/alembic.ini downgrade -1 # roll back one revision
```

### Fixing "relation already exists" / DuplicateTable

This means the table physically exists but Alembic's `alembic_version` table
still points at an older revision (e.g. a table was created out-of-band by an old
`create_all` startup). The tables are correct — Alembic just needs to be told the
migration is already applied, without re-running its `CREATE TABLE`:

```bash
alembic -c app/database/alembic.ini stamp head
```

Only use `stamp` when the existing table actually matches the migration. If it
doesn't, drop the stray table first, then `upgrade head` to create it properly.
## Wiping the dev database

Drops the whole schema and rebuilds it empty.

> **Not `alembic upgrade head`.** The migration history has no initial
> create-tables migration — the base tables were originally made by
> `create_all`, and Alembic only tracks deltas on top. So upgrading from an
> empty DB dies on the first `add_column`. Rebuild the tables from the models,
> then `stamp head` to mark Alembic as up to date.

```bash
# Local (Docker db + host venv)
docker exec sms_scrapers_api-db-1 psql -U sms_user -d 317_SMS -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
PYTHONPATH=app python -c "from database.models import Base; from database.database import engine; Base.metadata.create_all(engine)"
alembic -c app/database/alembic.ini stamp head

# Deployed dev: drop the schema, then re-sync. The migration Job sees an empty
# database and does the create_all + stamp itself.
kubectl -n sms-dev exec deploy/sms-api -- sh -c 'psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
kubectl -n argocd patch application sms-api-dev --type merge -p '{"operation":{"sync":{}}}'
kubectl -n sms-dev rollout restart deploy/sms-api
```

Only ever run this against dev — it is irreversible and takes the whole schema with it.

## Dev fake auth

Real auth needs a Google login plus a Workspace service account, which can't be
automated (e.g. for Playwright UI sweeps). A flag-gated bypass sidesteps it:

```bash
ENV=dev DEV_FAKE_AUTH=1 PYTHONPATH=app uvicorn api:app --reload
```

When `DEV_FAKE_AUTH=1`, `verify_token` accepts `Bearer dev-fake-token` as the
owner account (`OWNER_EMAIL`, role `staff`) — no Google round-trip. A role
suffix picks a different tier: `dev-fake-token:snco` and `dev-fake-token:nco`
log in as `dev.snco@` / `dev.nco@` with that role, so you can test what an NCO
or SNCO actually sees. It's inert unless the flag is set, so production is
unaffected (see `app/core/security.py`). The app also refuses to start with the
flag on unless `ENV=dev`, so a stray `DEV_FAKE_AUTH=1` in a deploy fails loudly
instead of opening the API. `ENV=dev` also turns on `/docs`, `/redoc` and
`/openapi.json`, which are hidden everywhere else.

The frontend has the matching flag: set `AUTH_DEV_BYPASS=1` in the UI's
`.env.local` and the login page shows Staff / SNCO / NCO buttons that issue the
matching token. **Both flags must be off in production.**

## NCO Holidays calendar (one-time setup)

NCO holiday bookings are mirrored onto a shared Google Calendar as all-day
events. Three things have to be in place, all outside this repo:

1. **The calendar.** In Google Calendar, create a calendar called
   *NCO Holidays* owned by a Workspace account (not a personal one). Copy its
   calendar ID from *Settings → Integrate calendar*.
2. **Share it with the service account.** Under *Share with specific people*,
   add `GOOGLE_IMPERSONATE_EMAIL` — the account the service account acts as —
   with **Make changes to events**. Without this, every write comes back 404.
3. **Add the scope to domain-wide delegation.** In the Admin console under
   *Security → API controls → Domain-wide delegation*, add
   `https://www.googleapis.com/auth/calendar.events` to the service account's
   existing scope list. This is a replace-the-whole-list field, so paste the
   current scopes back alongside the new one.

NCOs have to give at least two weeks' notice — the first day of a holiday must
be 14 days or more after the day they book it. Staff are exempt (they can only
ever book their own). The rule lives in `MIN_NOTICE_DAYS` in
`app/routers/nco_holidays.py`; the booking form reads it off the API rather than
hardcoding it, so changing that constant is enough.

Then set `NCO_HOLIDAY_CALENDAR_ID` in `deploy/base/config.env` and push. Until it's set, holidays still save in the SMS and the page shows a
"calendar not connected" banner — nothing is lost, and the **Retry** action on
each row pushes the backlog once the calendar is wired up.

## Contributing

- Branch off `main` and open a pull request back into it. `main` only accepts
  pull requests: no direct pushes, force-pushes or deletion.
- `pytest` (lint and tests) and `docker-build` must pass before merging.
- Changes under `.github/`, `deploy/` or `ops/` need a review from
  @BenMcD23 (`.github/CODEOWNERS`). Everything else merges on green CI.
- Don't branch off `development`. It carries the dev overlay's image tag, and
  a pull request from it would point prod at a dev image.
- Every change ships with tests. [`CLAUDE.md`](CLAUDE.md) has the conventions
  and the shared test fixtures.

The host lockout runbook (Tailscale watchdog, Cloudflare Tunnel) is in
[`docs/host-recovery.md`](docs/host-recovery.md).
