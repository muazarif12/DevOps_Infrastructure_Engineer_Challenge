# Deploying to Railway

This is the actual **live deployment path** for this submission — chosen because AWS/GCP
credentials for Part 2 were not received, and Railway lets the app run for real (see the
challenge FAQ: managed platforms are explicitly allowed if you "build operational tooling on
top," not just click Deploy).

`docker-compose.yml` and `infra/terraform/` are unchanged and still in the repo — they're the
"local full-stack review" and "AWS-when-credentials-exist" paths respectively, and still
demonstrate the complete operational design (Prometheus/Grafana/Loki/Alertmanager/backups).
Railway's PaaS model doesn't fit self-hosting that same stack cleanly (no host-level access for
node-exporter, no docker.sock for Promtail, and Railway already gives you per-service CPU/
memory graphs and a log viewer for free) — so **this guide covers worker + api + Postgres
only**, the part that needs to actually answer the phone number live.

## What changed in the code for this

- `app/store.py` now normalizes a bare `postgres://`/`postgresql://` URL (what Railway's managed
  Postgres hands you) to `postgresql+psycopg://` (the driver actually installed) — so
  `INTAKE_DB_URL` can just be Railway's `DATABASE_URL` reference directly, no manual editing.
- Nothing else needs to change — the same Dockerfile serves both services here, exactly like
  in `docker-compose.yml` (worker via its default `CMD`, api via a start-command override).

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
   `api`. (Same source, different service, different start command/variables — exactly how
   `docker-compose.yml` runs `worker` and `api` from the one image.)

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
actually listens on, matching how `docker-compose.yml` runs it.

**Variables:**
```
INTAKE_DB_URL=${{Postgres.DATABASE_URL}}
```

## 5. Deploy and verify

Both services redeploy automatically once their variables are set (or trigger manually from
the dashboard). First deploy of `worker` takes longer than usual (~60–90s) while it downloads
the turn-detector/VAD model cache into the container — expected, matches the same first-boot
behavior documented for the Compose path.

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

Basic up/down alerting on this deployment, chosen deliberately over re-hosting the full
Prometheus/Grafana/Alertmanager stack on Railway (see "Known gaps" below for why):

1. `worker`'s health port (`:8081`, the same `AgentServer` health endpoint used locally) has
   its own Railway public domain — separate from `api`'s — specifically so it can be monitored
   externally. It carries no PHI, just a `200`/`503` liveness signal.
2. An external uptime checker (e.g. UptimeRobot's free tier, 5-minute interval) polls three
   URLs and alerts on failure:
   - `https://<worker-domain>.up.railway.app/` — is the worker itself alive and registered
     with LiveKit; this is the one that actually predicts whether the phone number will be
     answered.
   - `https://<api-domain>.up.railway.app/health` — is the API process up at all.
   - `https://<api-domain>.up.railway.app/readyz` — does the API's database connection
     actually work; catches a Postgres problem that `/health` deliberately ignores.

This is real, external, zero-infrastructure alerting — not a dashboard nobody's watching. It
doesn't replicate the custom rule *logic* in `infra/observability/alert_rules.yml`
(`BackupStale`'s dead-man's-switch, `HighAPIErrorRate`'s threshold, etc.) — those stay
demonstrated against the Compose stack, and moving them here is the next step, not something
silently dropped.

## Operating this deployment

- **Logs**: each service's Railway dashboard tab has a live, searchable log viewer — the
  worker's logs are still structured JSON with the call's `room` name attached
  (`app/worker.py`), so filtering by that string in Railway's log search gets you the same
  "trace one call" capability described in `docs/RUNBOOK.md`, just via Railway's UI instead of
  Loki.
- **Metrics**: Railway's dashboard shows CPU/memory/network per service natively. The app's own
  `/metrics` (api) and `:9091/metrics` (worker) endpoints are still there if you want to point
  an external Prometheus at them later (Railway services can expose additional TCP ports via
  private networking) — just not self-hosted alongside on Railway in this pass.
- **Restarts / redeploys**: push to the connected GitHub branch, or use `railway up` from the
  CLI for a one-off manual deploy.
- **Rollback**: Railway keeps prior deployments per service — "Redeploy" an older one from the
  Deployments tab if a push breaks something.

## Known gaps versus the AWS/Compose design (honest, not hidden)

- **Basic up/down alerting only** — the external uptime check (see "Monitoring" above) tells
  you *that* something's wrong within ~5 minutes, but not the specific *why* that the custom
  Prometheus rules give (`BackupStale`'s dead-man's-switch, `HighAPIErrorRate`'s threshold,
  `WorkerLoadHigh`, `HostMemoryLow`). Those rules are real and tested against the local Compose
  stack, just not re-hosted against this deployment. Next step if needed: either deploy
  Prometheus + Alertmanager as two more Railway services scraping over private networking, or
  point the app's existing `/metrics` endpoints at Grafana Cloud's free tier instead.
- **No automated backups configured yet for this Postgres** — check Railway's own backup
  offering for your plan tier first (Railway provides some level of managed Postgres backup
  depending on plan); `scripts/backup.sh`/`restore.sh` still work unchanged against any
  S3-compatible endpoint if you want the same tested backup path here too, they just need
  somewhere to write to (Railway has no built-in object storage) — say the word and I'll wire
  that up as a scheduled Railway service next.
