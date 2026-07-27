# Deploying to Railway

This is the actual **live deployment path** for this submission — chosen because AWS/GCP
credentials for Part 2 were not received, and Railway lets the app run for real (see the
challenge FAQ: managed platforms are explicitly allowed if you "build operational tooling on
top," not just click Deploy).

`infra/terraform/` is the GCP-assuming-credentials-exist path (see `infra/GCP_DEPLOYMENT.md`),
separate from this one and still in the repo.

This project runs **five** Railway services: `worker` (answers calls), `Api` (REST access to
patient records), `Postgres` (managed database), and `prometheus` + `grafana` (self-hosted
monitoring — added after the initial deploy, once the basics were confirmed working; see
"Observability" below). `node-exporter`, `postgres-exporter`, Loki, and Alertmanager are
deliberately **not** deployed here — the first two need host-level access Railway's PaaS model
doesn't give you, and Alertmanager was scoped out in favor of the external uptime check
described under "Monitoring."

## What changed in the code for this

- `app/store.py` now normalizes a bare `postgres://`/`postgresql://` URL (what Railway's managed
  Postgres hands you) to `postgresql+psycopg://` (the driver actually installed) — so
  `INTAKE_DB_URL` can just be Railway's `DATABASE_URL` reference directly, no manual editing.
- Nothing else needs to change — the same root Dockerfile serves both `worker` and `api` here,
  just with different start commands (worker via its default `CMD`, api via an override).

## 1. Prerequisites

```bash
npm i -g @railway/cli
railway login          # opens a browser — do this yourself, this can't be automated headlessly
```

Push the repo to GitHub first if you haven't (see the main README) — deploying straight from a
GitHub repo is the simplest path and means every future `git push` can redeploy automatically.

## 2. Create the project and add Postgres

Via the [Railway dashboard](https://railway.app/new):
1. **New Project** → **Deploy from GitHub repo** → select this repo. Railway creates its first
   service from the Dockerfile — rename it to `worker` (Settings → name).
2. In the same project: **New** → **Database** → **Add PostgreSQL**. Railway provisions it and
   exposes `DATABASE_URL` (and the individual `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/
   `PGDATABASE` pieces) as variables on that Postgres service, referenceable from any other
   service in the project as `${{Postgres.DATABASE_URL}}`.
3. **New** → **GitHub Repo** → the same repo again, to create a second service — rename it
   `api`. (Same source, different service, different start command/variables.)

## 3. Configure `worker`

**Variables** (Settings → Variables):
```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_LOG_LEVEL=INFO
INTAKE_DB_URL=${{Postgres.DATABASE_URL}}
```

No public networking needed — the worker only dials LiveKit Cloud outbound; nothing needs to
reach it over the internet.

## 4. Configure `api`

**Settings → Deploy → Custom Start Command:**
```
uv run uvicorn app.web:app --host 0.0.0.0 --port 8000
```

**Settings → Networking:** generate a public domain, and set the target port to **8000**
explicitly (rather than relying on Railway's `$PORT` convention) — that's what the container
actually listens on.

> **Real incident hit during this deployment:** generating the domain without setting a target
> port left it as `targetPort: null`, and every request to it returned `502 Application failed
> to respond` — the container was healthy and listening on `:8000` the whole time (confirmed in
> its logs: `"Uvicorn running on http://0.0.0.0:8000"`), Railway's edge just didn't know which
> port to route to. Fixed with `railway domain update <domain> --port 8000 --service Api`. If
> `/health` 502s on a freshly created domain, check the target port first.

**Variables:**
```
INTAKE_DB_URL=${{Postgres.DATABASE_URL}}
```

## 5. Deploy and verify

Both services redeploy automatically once their variables are set (or trigger manually from
the dashboard). First deploy of `worker` takes longer than usual (~60–90s) while it downloads
the turn-detector/VAD model cache into the container — expected, not a hang.

```bash
curl https://<your-api-domain>.up.railway.app/health     # {"status": "up"}
curl https://<your-api-domain>.up.railway.app/readyz      # {"status": "ready"} — real DB round-trip
curl https://<your-api-domain>.up.railway.app/patients
```

> **Note:** `/patients*` has no authentication in this deployment — an API-key requirement was
> added mid-build and then deliberately rolled back to keep the app matching its original,
> pre-assessment behavior. Fine for this exercise; not fine for real PHI in production. See
> `docs/SECURITY.md` for what's actually needed before that (real per-user auth, not a single
> shared key) and the README's Known Risks.

Check `worker`'s Railway logs for `"registered worker" {"agent_name": "my-agent", ...}` — that's
confirmation it connected to LiveKit Cloud and is ready for dispatch. Then call the number.

## Monitoring

Two independent layers, both live:

1. **External uptime check** (e.g. UptimeRobot's free tier, 5-minute interval) polling three
   URLs, alerting on failure:
   - `https://<worker-domain>.up.railway.app/` — is the worker itself alive and registered
     with LiveKit; this is the one that actually predicts whether the phone number will be
     answered. (`worker`'s health port `:8081` — the same `AgentServer` endpoint used locally —
     has its own Railway public domain, separate from `api`'s, specifically for this. It carries
     no PHI, just a `200`/`503` liveness signal.)
   - `https://<api-domain>.up.railway.app/health` — is the API process up at all.
   - `https://<api-domain>.up.railway.app/readyz` — does the API's database connection
     actually work; catches a Postgres problem that `/health` deliberately ignores.

   This is real, external, zero-infrastructure alerting that survives even if Railway's own
   dashboards or the observability stack below go down.

2. **Self-hosted Prometheus + Grafana**, as two more Railway services — see below.

## Observability (Prometheus + Grafana)

Deployed as `prometheus` and `grafana` services in the same Railway project, scraping over
Railway's private networking. Not part of the initial deploy — added afterward once the core
system was confirmed working, since it needed its own build setup.

**How the config gets into the containers:** Railway builds from a Dockerfile, not arbitrary
volume mounts, so `infra/observability/prometheus/Dockerfile` and
`infra/observability/grafana/Dockerfile` each start `FROM` the stock image and `COPY` the repo's
config into it (`infra/observability/prometheus.yml`, `alert_rules.yml`, and the Grafana
provisioning/dashboard JSON under `infra/observability/grafana/`). Set each service's build
Dockerfile path to its respective file (same pattern as `worker`/`api` sharing one root
Dockerfile with different start commands — here it's different Dockerfiles instead).

> **Real incident hit setting this up:** both builds failed with `"/infra/observability/
> prometheus.yml": not found`, even though the file is right there in the repo. Cause:
> `.dockerignore` blanket-excluded `infra/` — fine when only the root app Dockerfile existed,
> but `.dockerignore` applies to *every* image built from this repo context, so it silently
> stripped the exact files these new Dockerfiles needed to `COPY`. Fixed by removing that
> exclusion (no secrets live under `infra/` — `terraform.tfvars` is separately gitignored).

**Scrape targets** (`infra/observability/prometheus.yml`): `worker.railway.internal:9091` and
`api.railway.internal:8000/metrics` — Railway's private-networking hostnames for those two
services (`<service-name>.railway.internal`, visible on each service's own Variables tab as
`RAILWAY_PRIVATE_DOMAIN`).

**Access:**
```bash
railway domain --service prometheus --port 9090 --json   # already done; URL in Railway dashboard
railway domain --service grafana --port 3000 --json       # already done; URL in Railway dashboard
railway variable list --service grafana --json             # GF_SECURITY_ADMIN_USER/PASSWORD
```

**Dashboard** (`infra/observability/grafana/dashboards/overview.json`) is intentionally trimmed
to 5 panels — Worker up/down, API up/down, and three API request/latency/error-rate graphs —
all confirmed showing real data. It originally had 11; the other 6 were removed rather than
left as permanent "No data" (Postgres/host-memory/disk needed exporters never deployed here;
see next paragraph for why the other three were cut).

**A real, verified SDK limitation, not a config mistake:** the worker's own metrics
(`lk_agents_worker_load`, `lk_agents_active_job_count`, `lk_agents_proc_initialize_duration_
seconds`) are correctly defined in the installed `livekit-agents` SDK with proper multiprocess-
mode Prometheus wiring (`livekit/agents/telemetry/metrics.py`, `telemetry/http_server.py`) — but
verified, by directly querying Prometheus, to never populate. This isn't "no data yet": even
`proc_initialize_duration_seconds`, tied to an event we *know* fired repeatedly (the worker's
own logs show `"process initialized"` multiple times), shows zero samples. The `/metrics`
endpoint itself is healthy (`200 OK`, correct `Content-Type`, just `Content-Length: 0`) — this
points at the cross-process multiprocess metrics file aggregation (`PROMETHEUS_MULTIPROC_DIR`)
not working correctly in this environment, for a reason that needs shelling into the container
to diagnose further, which isn't available through the Railway CLI. Only `up{job="worker"}`
(scrape-based liveness) is real; the richer load/capacity metrics are not, and the dashboard
was trimmed to reflect that honestly rather than show three permanently-empty panels.

The API's own metrics (`prometheus-fastapi-instrumentator` — `http_requests_total`,
`http_request_duration_seconds`) are a completely separate, unrelated implementation and were
confirmed working with real content — the SDK issue above is specific to the LiveKit worker's
internal metrics, not a Prometheus/Grafana setup problem.

## Operating this deployment

- **Logs**: each service's Railway dashboard tab has a live, searchable log viewer — the
  worker's logs are still structured JSON with the call's `room` name attached
  (`app/worker.py`), so filtering by that string in Railway's log search gets you the same
  "trace one call" capability described in `docs/RUNBOOK.md`.
- **Metrics**: both Railway's own per-service CPU/memory/network graphs (native, zero setup)
  and the self-hosted Prometheus/Grafana above are available.
- **Restarts / redeploys**: push to the connected GitHub branch, or use `railway up` from the
  CLI for a one-off manual deploy.
  - **Real incident**: a push once got stuck with `"Deployment queued due to upstream GitHub
    issues"` for 10+ minutes, even with no reported outage on GitHub's or Railway's own status
    pages, and survived a full delete-and-recreate of the service. Fixed by deploying directly
    via `railway up --service <name>` (uploads the current directory straight to Railway,
    bypassing the GitHub webhook path entirely) rather than continuing to wait on or retry the
    stuck webhook-triggered build.
- **Rollback**: Railway keeps prior deployments per service — "Redeploy" an older one from the
  Deployments tab if a push breaks something.
- **Memory**: the worker's inference subprocess needs roughly 2-2.5GB of RAM. Railway's Trial
  plan caps a service around 1GB, which OOM-kills it during startup every time (visible as
  `DuplexClosed`/`"failed to get memory info for process"` in the worker's logs, immediately
  after `"starting inference executor"`) — this isn't a code bug, it's a plan limit. Fixed here
  by upgrading off the Trial plan; Hobby's default 8GB limit has comfortable headroom.

## Known gaps versus the fuller GCP/Terraform design (honest, not hidden)

- **No alert *delivery* beyond the external uptime check** — `alert_rules.yml`'s rules
  (`BackupStale`'s dead-man's-switch, `HighAPIErrorRate`'s threshold, `WorkerLoadHigh`,
  `HostMemoryLow`) are loaded into the deployed Prometheus and evaluate on its own `/alerts`
  page, but nothing routes them anywhere (no Alertmanager deployed) — and `WorkerLoadHigh`
  specifically can never fire regardless, since it depends on the `lk_agents_worker_load` metric
  documented above as not populating. Next step: deploy Alertmanager as a third observability
  service, or point `/metrics` at Grafana Cloud's free tier for hosted alerting instead.
- **No automated backups configured yet for this Postgres** — check Railway's own backup
  offering for your plan tier first (Railway provides some level of managed Postgres backup
  depending on plan); `scripts/backup.sh`/`restore.sh` still work unchanged against any
  S3-compatible endpoint if you want the same tested backup path here too, they just need
  somewhere to write to (Railway has no built-in object storage) — say the word and I'll wire
  that up as a scheduled Railway service next.
