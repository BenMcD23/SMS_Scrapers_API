# Deploying the API to Kubernetes

The API runs on a three-node cluster, one node per site. Everything in this
directory is applied by Argo CD from git; nothing is `kubectl apply`'d by CI.

```
deploy/
├── base/            # what runs: API, migrations, Postgres, tunnel
├── overlays/
│   ├── prod/        # namespace sms, image tag = VERSION, backups on
│   └── dev/         # namespace sms-dev, image tag = dev-<sha>, one Postgres
└── argocd/          # the two Argo CD Applications that watch the overlays
```

| Component | What | Why this one |
|---|---|---|
| API | `Deployment` × 1, `Recreate` strategy | See "Why one replica" |
| Migrations | `Job` as an Argo CD PreSync hook | A failed migration fails the sync and leaves the old API up |
| Database | CloudNativePG `Cluster`, 3 instances, one per zone | Streaming replication + automatic failover, in-cluster, free |
| Ingress | `cloudflared` × 2 with a remotely-managed tunnel | Outbound only, no port forwards, hostname survives a site going down |
| Deploys | Argo CD auto-sync from `deploy/overlays/*` | Self-healing, auditable, no cluster credentials in GitHub |

## How a release happens

1. Bump `VERSION` (e.g. `1.0.0` → `1.1.0`) and push to `main`.
2. `release.yml` builds `ghcr.io/benmcd23/sms-api:<VERSION>`, sets that tag in
   `deploy/overlays/prod/kustomization.yaml`, commits it back to `main`, and
   tags the repo `v<VERSION>`.
3. Argo CD notices the overlay changed (it polls every 3 minutes; add the
   GitHub webhook to make it instant), runs the migration Job, then rolls the
   API.

A push to `main` that does **not** touch `VERSION` deploys nothing. Every push
to `development` builds `:dev-<sha>` and deploys it to `sms-dev` the same way.

Roll back by setting `newTag` in the overlay to a previous version and pushing.

## One-time cluster setup

### 1. Label the nodes with their site

The Postgres anti-affinity and the tunnel spread both key off the zone label.

```bash
kubectl label node <node-a> topology.kubernetes.io/zone=site-a
kubectl label node <node-b> topology.kubernetes.io/zone=site-b
kubectl label node <node-c> topology.kubernetes.io/zone=site-c
```

### 2. Storage

CloudNativePG needs a `StorageClass`. On k3s the bundled `local-path` is fine
(each Postgres instance keeps its own copy of the data on its own node, so
local disks are exactly right). Set `spec.storage.storageClass` in
`base/database.yaml` if your default class is something else.

### 3. Install the operators

```bash
# CloudNativePG (pin to the current release; check the project's releases page)
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.0.yaml

# Argo CD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

### 4. Secrets (never in git)

Per namespace (`sms`, `sms-dev`):

```bash
# Everything the API reads from .env, minus DATABASE_URL (CNPG provides it).
kubectl -n sms create secret generic sms-api-env --from-env-file=.env

# Tunnel token from Cloudflare Zero Trust → Networks → Tunnels → your tunnel.
kubectl -n sms create secret generic cloudflared-token --from-literal=token=<TUNNEL_TOKEN>
```

`DATABASE_URL` must **not** be in that `.env`: the Deployment takes it from the
`sms-db-app` Secret CloudNativePG generates, which always points at the
current primary. If it is present it is harmless (the explicit `env` entry
wins) but confusing.

To change a value later: `kubectl -n sms edit secret sms-api-env` (values are
base64) or recreate it, then `kubectl -n sms rollout restart deployment/sms-api`.
Argo CD's `selfHeal` will not undo this because it does not manage the Secret.

### 5. Cloudflare Tunnel

In Zero Trust → Networks → Tunnels create a tunnel per environment and add a
public hostname:

| Environment | Hostname | Service |
|---|---|---|
| prod | `api.317atc.co.uk` | `http://sms-api.sms.svc.cluster.local:8000` |
| dev | `api-dev.317atc.co.uk` | `http://sms-api.sms-dev.svc.cluster.local:8000` |

Then set `NEXT_PUBLIC_API_BASE` on the Vercel projects to those hostnames.
Consider a Cloudflare Access policy on the dev hostname.

### 6. Register the Applications

```bash
kubectl apply -f deploy/argocd/
```

Argo CD creates the namespaces and syncs. Watch with `kubectl -n argocd get
applications` or the Argo CD UI (`kubectl -n argocd port-forward svc/argocd-server 8080:443`).

## Migrating the data from the old server

The Drive backups are plain `pg_dump --clean --if-exists` output, so the
simplest route is a restore:

```bash
# On the old server
docker exec sms-prod-db-1 pg_dump -U sms_user --clean --if-exists 317_SMS | gzip > sms.sql.gz

# Into the cluster (the -rw service is the primary)
kubectl -n sms port-forward svc/sms-db-rw 5433:5432 &
PGPASSWORD="$(kubectl -n sms get secret sms-db-app -o jsonpath='{.data.password}' | base64 -d)" \
  zcat sms.sql.gz | psql -h localhost -p 5433 -U sms_user 317_SMS
```

Alternatively uncomment the `import` block in `base/database.yaml` before the
first sync and CloudNativePG will pull the data itself over the network.

The API's `/backups` page and nightly Drive dump keep working unchanged: they
use `pg_dump`/`psql` against `DATABASE_URL`, which now resolves to `sms-db-rw`.

## Cutover checklist

1. Cluster set up as above; `sms-dev` synced and reachable on its hostname.
2. Point the dev Vercel project at `api-dev.317atc.co.uk`, smoke test.
3. Stop the old prod stack (`docker compose -p sms-prod stop api`) so no writes
   land after the dump, take the dump, restore it into `sms`.
4. Point the prod Vercel project at `api.317atc.co.uk`.
5. Delete `.github/workflows/deploy.yml`, the `deploy-legacy` job in
   `sync-main-to-dev.yml`, `docker-compose.{prod,dev}.yml`, `serve-config.json`
   and `ops/` (all single-server Tailscale tooling). Keep `docker-compose.yml`
   and `docker-compose.local.yml` for local development.

## Why one replica

The API is not stateless yet:

- A running Bader scrape lives in the process that started it (log buffer,
  stop flag, Playwright context), and the SMS site tails it over SSE. A second
  replica behind the Service would answer "nothing running" half the time.
- The scheduler (parade-night texts, cleanups, backups, scheduled scrapes) is
  in-process. Two replicas would send every text twice.

So the Deployment is `replicas: 1` with `strategy: Recreate`. Availability
comes from Kubernetes rescheduling the pod onto a surviving node when a site
goes down, which the `not-ready`/`unreachable` tolerations bring down to about
30 seconds plus image pull. The database fails over independently.

To scale out later: move scraper run state into the `Scraper_Runs` table
(logs are already persisted there at the end of a run) and stream from the
database, then either run the scheduler in a separate one-replica Deployment
(`SCHEDULER_ENABLED=false` on the API) or add a lease so only one replica runs
jobs. The `SCHEDULER_ENABLED` flag and the 5-minute schedule reconcile already
exist for that split.

## Day-to-day

```bash
kubectl -n sms get pods                            # is it up
kubectl -n sms logs deploy/sms-api -f              # API logs
kubectl -n sms get cluster sms-db                  # Postgres primary/replicas
kubectl -n sms exec -it sms-db-1 -- psql 317_SMS   # a psql shell
kubectl -n argocd get app sms-api-prod             # sync state
```

Force a deploy without a version bump (dev only, or an emergency):
`argocd app sync sms-api-prod` or press *Sync* in the UI.
