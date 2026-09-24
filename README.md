# SMS Scrapers API

FastAPI backend for the 317 SMS site - handles scrapers, assessments, stores, and cadet management.

## Prerequisites

- Docker & Docker Compose (local dev only)
- `kubectl` and `kubeseal` with the homelab kubeconfig, for anything touching
  the deployed environments

## Environments

Both run on the k3s homelab cluster, deployed by Argo CD from `deploy/`:

| | Branch | Namespace | URL (Tailscale Funnel) |
|-|--------|-----------|------------------------|
| **prod** | `main` | `sms-prod` | `https://sms-api.<tailnet>.ts.net` |
| **dev** | `development` | `sms-dev` | `https://sms-api-dev.<tailnet>.ts.net` |

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
primary (and the API with it) lives on squadron, except 17:00–23:00 on
Wednesday and Friday, when it moves to home.

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

## Never getting locked out again

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

`CLOUDFLARE_TUNNEL_TOKEN` is not used by the app or the cluster. It is a leftover. The tunnel belongs on the host as a systemd
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

## Local dev (without Docker)

Lint and tests: `ruff check app` and `pytest` (see below).

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

Start PostgreSQL (the local override publishes it on `localhost:5432`, and it
reads `POSTGRES_PASSWORD` from `.env`), then run migrations:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db
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
DEV_FAKE_AUTH=1 PYTHONPATH=app uvicorn api:app --reload
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

## Database Migrations (Alembic)

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
