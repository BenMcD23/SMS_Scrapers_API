# SMS Scrapers API

FastAPI backend for the 317 SMS site - handles scrapers, assessments, stores, and cadet management.

## Architecture

Everything runs in the cloud except the scrapers, which need a browser and
Bader credentials and so stay at home:

```
Vercel (UI)  ──►  Lambda Function URL (FastAPI + Mangum)  ──►  Neon Postgres
                            │                                      ▲
                  EventBridge Scheduler                            │ outbound only
                  (daily/weekly jobs)                              │
                                                       home box: worker (Playwright)
```

**The home box only makes outbound connections.** It doesn't serve the API and
nothing has to be able to reach it: it claims rows from the `Scraper_Jobs`
queue in Postgres, runs the scrape, and writes the logs and results back. A
scrape is requested by the API inserting a queued row (`routers/scrapers.py`)
and executed by the worker claiming it (`app/worker.py`).

This is the whole point of the layout. Tailscale used to be the public front
door — `serve-config.json` funnelled `:443` straight at `api:8000` — so every
Tailscale wobble took the site down. Now Tailscale is an admin convenience for
SSH and deploys, and **losing the home box degrades the site to "scrapers
unavailable" rather than "down"**. Test that deliberately (pull the box off the
network) rather than assuming it.

Consequences worth knowing before changing things here:

- **Playwright must never be imported by the API.** It's confined to
  `app/worker.py`, `scripts/scraper_calls.py` and the modules those import.
  `scripts/ji_ao_generator.py` (used by `routers/events.py`) doesn't use it and
  stays in the cloud.
- **No in-process caches for shared data.** A Lambda container only caches for
  itself, so an edit would appear to some users and not others. The token and
  role caches in `core/security.py` are the exception — stale there is a
  missed optimisation, not a wrong answer.
- **Uploads are capped at ~6 MB** by Lambda's payload limit. The UI compresses
  images before sending (`lib/compress-image.ts`) and rejects what it can't get
  under the cap.

## Prerequisites

- Docker & Docker Compose (for the home worker)
- A Neon project, an ECR repository and a Lambda function (see *Cloud setup*)

## Environments

Two worker stacks run on the home server simultaneously, each pointed at its
own Neon database and paired with its own Lambda:

- **prod** — `main` branch, `tailscale-prod`, Neon primary branch
- **dev**  — `development` branch, `tailscale-dev`, Neon `dev` branch

Neither publishes a port. Deployments are handled automatically by GitHub
Actions on push to either branch (`.github/workflows/deploy.yml`): migrations
run once in CI against Neon, then the Lambda and the worker are updated.

## Cloud setup (one-time)

### 1. Neon

Create a project on the free tier. Two connection strings matter, and they are
not interchangeable:

- **Pooled** (the `-pooler` host) → the **Lambda**. Lambda opens many
  short-lived connections and PgBouncer is what makes that safe.
- **Direct** → **Alembic migrations only**, run from CI. Never from Lambda.

Use a Neon **branch** for dev rather than a second project: it gives a dev
database carrying prod's data for free, instead of a seeded one to maintain.

To move an existing database in, restore the `pg_dump` output into Neon. The
dump carries `alembic_version`, so `alembic current` afterwards should report
the same head it did at home.

### 2. Lambda

Build from `Dockerfile.api` (no Chromium — that's `Dockerfile.worker`) and push
to ECR. Then:

- Use a **Function URL**, not API Gateway: no 29-second timeout ceiling, and no
  per-request charge.
- **Leave the Function URL's own CORS empty.** FastAPI's `CORSMiddleware`
  already allows `sms.317atc.co.uk` and the anchored Vercel preview pattern;
  configuring both layers sends two `Access-Control-Allow-Origin` headers,
  which browsers reject outright.
- Set `DATABASE_URL` to the **pooled** Neon URL, plus the same secrets the home
  `.env` carries.
- Give it enough memory for the PDF/document generators (1024 MB is a sane
  start) and a timeout comfortably above the slowest form generator.

`Dockerfile.api` pins `postgresql-client-17` via the `PG_MAJOR` build arg. It
**must** match Neon's server major version or `pg_dump` refuses to run and the
backups fail silently — check with `SELECT version()` and bump it when Neon
upgrades.

### 3. EventBridge Scheduler

The app lifespan can't hold a scheduler on Lambda, so each job is a rule that
invokes the same function with a `{"job": "<name>"}` payload;
`app/lambda_handler.py` dispatches it via `core/jobs.py`.

| Rule payload | Schedule (Europe/London) | What it does |
| --- | --- | --- |
| `{"job": "cleanup_orders"}` | daily | drops completed stores orders > 182 days |
| `{"job": "cleanup_assessments"}` | daily | drops uploaded assessment sheets > 182 days |
| `{"job": "cleanup_run_logs"}` | daily | purges scraper runs and job logs > 7 days |
| `{"job": "quali_expiry"}` | Fri 07:00 | emails qualifications expiring within 3 months |
| `{"job": "parade_texts"}` | Tue/Thu 16:00 | sends the parade-night text for the next day |
| `{"job": "db_backup"}` | daily 03:00 | `pg_dump` → Google Drive |
| `{"job": "keep_warm"}` | every 5 min | keeps a container warm (cold starts pay a Google round trip on token verification) |

Trigger each rule manually once after setting them up. `db_backup` is the one
to watch: **Neon's free tier has no downloadable backups**, so the Drive dump
is the only recovery path, and it now runs somewhere nobody is watching.

The scraper *schedules* are not here — they live on the worker
(`app/worker.py`), so a cloud outage doesn't stop a scheduled scrape.

## Server setup (one-time)

```bash
# Prod
mkdir -p ~/sms-api/prod && cd ~/sms-api/prod
git clone https://github.com/BenMcD23/SMS_Scrapers_API.git .
git checkout main
cp .env.tmpl .env  # fill in secrets; DATABASE_URL is Neon's DIRECT url

# Dev
mkdir -p ~/sms-api/dev && cd ~/sms-api/dev
git clone https://github.com/BenMcD23/SMS_Scrapers_API.git .
git checkout development
cp .env.tmpl .env  # DATABASE_URL points at the Neon dev branch
```

The worker holds a handful of long-lived connections, so it uses the **direct**
Neon URL. The pooled one is for the Lambda.

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

Stopping a worker stops scrapes; it does not affect the site.

## Updating environment variables

The `worker` service reads its config from `.env` via `env_file`, and that file
is loaded only when the container is **created** — a plain `docker compose
restart worker` keeps the old values. After editing `.env` on the server,
recreate the worker container so it picks up the new values:

```bash
# Prod
cd ~/sms-api/prod
nano .env
docker compose -p sms-prod -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate worker

# Dev
cd ~/sms-api/dev
nano .env
docker compose -p sms-dev -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate worker
```

`--force-recreate` is required — a change to the *contents* of `.env` doesn't
reliably trigger a recreate on its own. `.env` is not in git, so a code deploy
alone never adds new vars; edit it on the server first.

API-side env vars (`OC_EMAIL`, `COMMITTEE_EMAIL`, the Google credentials, …)
now live on the **Lambda function's** configuration, not in this `.env` — set
them there and the next invocation picks them up. (`NEXT_PUBLIC_*` vars like
`NEXT_PUBLIC_OC_EMAIL` live with the frontend host and are baked in at build
time — set them there and redeploy the UI.)

## Cutover, and how to roll back

The move was sequenced so each step could be undone on its own:

1. Restore into Neon and point the **existing home stack** at it. Run like that
   for a few days — this de-risks the database move before any Lambda exists.
2. Deploy the Lambda and smoke-test it against Neon while the home API still
   serves production.
3. Deploy the worker. Set `ENABLE_LOCAL_SCHEDULER=false` on any home API still
   running, or its APScheduler and the EventBridge rules both fire the same
   jobs.
4. Repoint `NEXT_PUBLIC_API_BASE` at the Function URL and redeploy the UI.
5. Stop the home `api` and `db` containers; Funnel is already gone from this
   repo (`serve-config.json` was deleted).

**Rollback** is repointing `NEXT_PUBLIC_API_BASE` back and restarting the old
containers. That needs the home database, so **keep the `postgres_data` volume
for at least a fortnight.** It is no longer referenced by `docker-compose.yml`,
which means it is easy to forget and easy to `docker volume prune` by accident:

```bash
docker volume ls | grep postgres_data   # confirm it's still there
```

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

Then set `NCO_HOLIDAY_CALENDAR_ID` on the Lambda function (it's an API-side
setting). Until it's set, holidays still save in the SMS and the page shows a
"calendar not connected" banner — nothing is lost, and the **Retry** action on
each row pushes the backlog once the calendar is wired up.

## Authorising Tailscale (first run)

After starting, the Tailscale containers need to be logged in once:

```bash
docker exec tailscale-prod tailscale up --accept-dns=false
docker exec tailscale-dev  tailscale up --accept-dns=false
```

Open the printed login URLs in a browser. State is persisted in `./tailscale_data` so this only needs to be done once per container.

Note these containers keep their own Tailscale state, entirely separate from the
**host's** Tailscale node (the one SSH uses). Logging one out does not affect the
other.

## Never getting locked out again

> Since the API moved to the cloud, none of this is load-bearing for the *site*
> — losing Tailscale now only pauses scrapers. It's kept because being locked
> out of the box is still a bad afternoon, but treat it as host hygiene rather
> than as production infrastructure.

In July 2026 an interactive `tailscale login` was run on the host over the very
SSH session that depended on it. The session died before the browser flow was
completed, the node dropped off the tailnet, and — with Tailscale as the only
route in and no out-of-band console on that box — there was no way back.

Three separate things had to be true for that to be unrecoverable, so the fix
addresses all three.

### 1. Install the watchdog and guard (`ops/`)

Create a Tailscale **OAuth client** at
[login.tailscale.com/admin/settings/oauth](https://login.tailscale.com/admin/settings/oauth)
with scope `auth_keys` (write) and tag `tag:server`. Use an OAuth client rather
than a plain auth key — reusable auth keys expire after 90 days and would
silently rot; OAuth clients do not expire.

```bash
cd ~/sms-api/prod
sudo ./ops/install-recovery.sh tskey-client-XXXXXXXXXXXX
```

That installs:

- **`tailscale-watchdog.sh`** on a systemd timer, every 2 minutes and 30s after
  boot. If the node is in `NeedsLogin`, `NoState` or `Stopped` it re-authenticates
  non-interactively from the stored credential. Any state it does not positively
  recognise — including an unreadable status or a missing `jq` — is treated as
  "cannot tell" and it takes no action, so a healthy node is never churned.
- **A guard wrapper** at `/usr/local/bin/tailscale`, which shadows the real
  binary for both plain and `sudo` calls. It refuses `login`/`logout`/`down`/
  `up`/`set` from an interactive SSH session that is not inside tmux, and tells
  you what to run instead. Scripts, systemd and CI are unaffected.

Two manual follow-ups the installer cannot do:

- **ACLs.** Re-authenticating with a tag makes the node tailnet-owned rather
  than user-owned, so your existing personal grants stop applying. Add
  `tagOwners`, a `grants` entry and an `ssh` rule for `tag:server` — the exact
  snippet is printed at the end of the install.
- **Connect by MagicDNS name, not IP** (`ssh server317@server317`). Tagging can
  change the node's `100.x` address.

Pause the watchdog for deliberate maintenance with
`sudo touch /etc/tailscale-recovery/paused`, and check on it with:

```bash
systemctl list-timers tailscale-watchdog.timer
journalctl -t tailscale-watchdog -n 20
```

### 2. Set up a second, non-Tailscale way in

The watchdog only helps when Tailscale fails *accidentally*. It cannot help if
the credential is revoked, the ACLs lock you out, or the Tailscale control plane
itself is the problem. You need one path that does not involve Tailscale at all.

A Cloudflare Tunnel is the right fit here: it is **outbound-only**, so it needs
no port forward and survives anything that happens to the tailnet. Because
token-based tunnels are configured from the dashboard, you can also add or
repoint a route *without touching the server* — which is exactly the capability
that was missing during the lockout.

Install `cloudflared` on the host (not in a container — it needs the host's
port 22), add a public hostname routed to `ssh://localhost:22`, and put a
Cloudflare Access policy in front of it so it is not exposed to the world. Then:

```bash
ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.317atc.co.uk" server317@localhost
```

`CLOUDFLARE_TUNNEL_TOKEN` already exists in `.env.tmpl` but is not wired into any
compose file — it is a leftover. The tunnel belongs on the host as a systemd
service, not in the stack, so that a broken deploy cannot take your recovery
path down with it.

### 3. Keep a break-glass channel (optional)

Both paths above are inbound. If you want something that works even when every
inbound route is dead, a cron on the box that polls a private repo every few
minutes and runs a `recovery.sh` if present is fully outbound and cannot be
locked out.

Be deliberate about this one: it is effectively remote code execution on the
server for anyone with write access to that repo. Use a dedicated private repo
with tight permissions, or skip it.

### Day-to-day rules

- Never run `tailscale login`/`logout`/`down` from a bare SSH session. The guard
  now blocks it, but the habit matters more than the guard.
- Prefer `tailscale up --auth-key=...` over the interactive browser flow — it
  cannot be orphaned by a dropped session.
- Do long or risky host work inside `tmux` so a dropped connection never strands
  a half-finished command.
- Disable key expiry on the server node in the admin console.

## Logs

```bash
# The worker (claim loop, scrape progress, watchdog kills)
cd ~/sms-api/prod && docker compose -p sms-prod logs -f worker
cd ~/sms-api/dev  && docker compose -p sms-dev  logs -f worker
```

API logs are in CloudWatch now, under the Lambda function's log group. A
scraper run's own logs are in the database either way: live in
`Scraper_Job_Logs` (what the UI polls) and summarised on the `Scraper_Runs` row
afterwards, which is what `/api-logs` shows.

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

Locally the API is still a normal uvicorn process — Mangum and
`lambda_handler.py` are only used by the deployed image. The scheduled jobs run
in-process here, as they always did; set `ENABLE_LOCAL_SCHEDULER=false` to
silence them.

To run a scraper locally you also need the worker, in a second terminal:

```bash
PYTHONPATH=app:. python -m worker
```

Without it, pressing *Run* queues a job that nothing ever claims — the UI will
sit at "Queued", which is exactly what it does when the home box is offline.

## Tests

```bash
pip install -r requirements-dev.txt
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
database, network or credentials are needed. The suite runs on every push and
pull request via `.github/workflows/tests.yml`.

## Dev fake auth (local UI testing)

Real auth needs a Google login plus a Workspace service account, which can't be
automated (e.g. for Playwright UI sweeps). A flag-gated bypass sidesteps it:

```bash
DEV_FAKE_AUTH=1 PYTHONPATH=app:. uvicorn api:app --reload
```

When `DEV_FAKE_AUTH=1`, `verify_token` accepts `Bearer dev-fake-token` as the
owner account (`OWNER_EMAIL`, role `staff`) — no Google round-trip. A role
suffix picks a different tier: `dev-fake-token:snco` and `dev-fake-token:nco`
log in as `dev.snco@` / `dev.nco@` with that role, so you can test what an NCO
or SNCO actually sees. It's inert unless the flag is set, so production is
unaffected (see `app/core/security.py`).

The frontend has the matching flag: set `AUTH_DEV_BYPASS=1` in the UI's
`.env.local` and the login page shows Staff / SNCO / NCO buttons that issue the
matching token. **Both flags must be off in production.**

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

**Neon dev branch** (`psql` against the dev branch's direct URL):

```bash
psql "$NEON_DEV_DIRECT_URL" -c "
INSERT INTO \"Cadets\" (cin, first_name, last_name, email, rank, flight, banned)
VALUES (9999999999, 'Ben', 'McDonald', 'ci.mcdonald@317atc.co.uk', 'Cadet', 'A', false)
ON CONFLICT (cin) DO NOTHING;
"
```

The `ON CONFLICT DO NOTHING` makes it safe to re-run. To remove the test cadet afterwards:

```bash
# local
docker exec sms_scrapers_api-db-1 psql -U sms_user -d 317_SMS -c "DELETE FROM \"Cadets\" WHERE cin = 9999999999;"

# Neon dev branch
psql "$NEON_DEV_DIRECT_URL" -c "DELETE FROM \"Cadets\" WHERE cin = 9999999999;"
```

## Resetting the dev database

Dev is a **Neon branch**, so the quickest reset isn't a wipe at all: delete the
branch and re-create it from the primary in the Neon console. That gives dev
prod's current data and prod's `alembic_version` in one step, with no seeding.

Wiping to an *empty* schema is still occasionally useful (local, mostly), and
drops everything.

> **Not `alembic upgrade head`.** The migration history has no initial
> create-tables migration — the base tables were originally made by
> `create_all`, and Alembic only tracks deltas on top. So upgrading from an
> empty DB dies on the first `add_column`. Rebuild the tables from the models,
> then `stamp head` to mark Alembic as up to date.

```bash
# Local (Docker db + host venv)
docker exec sms_scrapers_api-db-1 psql -U sms_user -d 317_SMS -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
PYTHONPATH=app:. python -c "from database.models import Base; from database.database import engine; Base.metadata.create_all(engine)"
alembic -c database/alembic.ini stamp head

# Neon dev branch (host venv, DATABASE_URL = the dev branch's DIRECT url)
psql "$NEON_DEV_DIRECT_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
DATABASE_URL="$NEON_DEV_DIRECT_URL" PYTHONPATH=app:. \
  python -c "from database.models import Base; from database.database import engine; Base.metadata.create_all(engine)"
DATABASE_URL="$NEON_DEV_DIRECT_URL" alembic -c database/alembic.ini stamp head
```

Only ever run this against dev — it is irreversible and takes the whole schema with it.

## Database Migrations (Alembic)

**Alembic is the single source of truth for the schema.** The app never calls
`Base.metadata.create_all()`. Migrations run **once in CI**, in the `migrate`
job of `.github/workflows/deploy.yml`, against Neon's **direct** URL — the
pooled endpoint can't run the session-level statements migrations need.

They run there rather than in either container because there are now two deploy
targets (the Lambda and the home worker) sharing one schema: if each ran its
own upgrade, a partial deploy could leave new code against an old schema. Both
deploy jobs depend on `migrate`, so neither starts until the schema is current.

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

Partial indexes (the one guarding the job queue) are declared with both
`postgresql_where` and `sqlite_where` so the test suite exercises the same
constraint production relies on.

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
