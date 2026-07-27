# Runbook

Operational procedures for the patient intake voice agent stack. Assumes the Compose stack
from the repo root (`docker compose ...`); swap in SSM/EC2 commands where noted for the AWS
deployment.

## Agent not answering calls

**Symptom:** Alertmanager shows `WorkerDown`, or a test call rings through to nothing / an
error message from the agent.

1. `docker compose ps worker` — is it running at all? If it's restarting in a loop, `docker
   compose logs worker --tail 200` and look for the startup traceback. The two most likely
   causes, both already fixed once in this repo's history and worth re-checking first:
   - Turn-detector model missing (`RuntimeError: ... Could not find file "model_q8.onnx"`) —
     means the Dockerfile's `HF_HOME` fix regressed; check it's still set before the
     `download-files` step.
   - `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` missing or wrong — worker will log a
     `ValueError` on startup and exit.
2. If the container is healthy but calls still don't connect, check the worker actually
   registered: `docker compose logs worker | grep "registered worker"`. If that line never
   appears, it's a LiveKit Cloud connectivity problem (egress to `wss://*.livekit.cloud`
   blocked?), not an app bug.
3. Check the LiveKit Cloud dashboard's Telephony → Calls page for the number. If calls show up
   there with an error but never reach this worker, the dispatch rule's agent name doesn't
   match `DISPATCH_AGENT_NAME` in `app/worker.py` (`"my-agent"`).
4. Check Grafana's "Worker" stat panel / `curl localhost:9090` (Prometheus) for `up{job="worker"}`
   — confirms whether Prometheus can even reach the worker's health port, independent of
   whether LiveKit itself thinks the worker is registered.

## High latency / slow responses on a call

1. Grafana → "API p95 Latency" panel is for the REST API only, not call audio latency — the
   voice pipeline's per-turn timing (`end_of_turn`, `llm_ttft`, `tts_ttfb`, `e2e`) is only
   visible in the worker's own logs today (see **Next Steps** in the README — this is the one
   piece of telemetry not yet wired to Prometheus/Grafana, since the SDK's metrics-event API in
   the pinned `livekit-agents` version turned out to have no active producer; see the README's
   trade-offs section for why).
2. `docker compose logs worker | grep -E "llm_ttft|tts_ttfb|e2e"` around the time in question.
3. Check `WorkerLoadHigh` / `HostMemoryLow` in Alertmanager — sustained high CPU/memory
   pressure on the single host is the most likely cause of a real (not perceived) slowdown, and
   it's shared across every service in `docker-compose.yml`, not just the worker.
4. First-call-after-restart latency is expected and not a bug: `num_idle_processes` defaults to
   0 in dev mode, several seconds in prod — see `docker-compose.yml`'s use of `start` (not
   `dev`), which restores production pre-warming.

## Database down

**Symptom:** `PostgresDown` fires, or `/readyz` returns 503.

1. `docker compose ps postgres` — crashed, OOM-killed, or disk-full? `docker compose logs
   postgres --tail 100`.
2. `DiskSpaceLow` firing at the same time strongly suggests disk exhaustion — Postgres,
   Prometheus, and Loki all write to local disk on this single host.
3. Once Postgres is back: `curl localhost:8000/readyz` should return `{"status": "ready"}`.
   The worker and api containers use `pool_pre_ping=True` (`app/store.py`), so they reconnect
   automatically — no restart needed once the database itself is reachable again.
4. Calls that failed to save during the outage were NOT silently lost: `save_record`'s
   exception path logs the full traceback (`app/flow.py`, `log.exception("save_record
   failed", ...)`), tagged with the call's room name — see **Trace one call** below to recover
   what a specific caller was trying to register, even though it never made it into Postgres.

## Restore from backup

> **Honesty note on this section:** Docker was not available in the sandbox this stack was
> built in, so the restore drill below could not actually be executed and its output pasted in
> as the original plan intended — this describes the exact procedure to run and what to expect,
> based on static review of `scripts/backup.sh`/`scripts/restore.sh`, not a captured real run.
> **Run this yourself before trusting it in an incident** — an untested restore path is the
> single biggest risk called out in the README.

1. Confirm backups are actually happening: Grafana's "Backup Age" panel, or
   `curl -s localhost:9090/api/v1/query --data-urlencode
   'query=time()-backup_last_success_timestamp_seconds'`.
2. List what's in the bucket:
   ```
   docker compose --profile local-s3 run --rm backup \
     python3 s3_object.py latest-key --bucket "$BACKUP_BUCKET" --prefix backups/
   ```
3. Restore the latest backup (safe to run against a live database — the dump uses `pg_dump
   --clean --if-exists`, so it drops-and-recreates rather than erroring on existing tables):
   ```
   docker compose --profile local-s3 run --rm backup ./restore.sh
   ```
   To restore a specific point-in-time instead of the latest: `./restore.sh backups/<timestamp>.sql.gz`.
4. Verify: `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) from patient_records;"`.
5. In a real incident (not a drill), stop `api` and `worker` first (`docker compose stop api
   worker`) so nothing writes mid-restore, then start them again afterward.

## Rollback a bad deploy

Given how the stack is built (single `Dockerfile`, image tagged implicitly as `latest` per
service by Compose):

1. `git log --oneline` to find the last known-good commit, `git checkout <sha>`.
2. `docker compose up -d --build worker api` — rebuilds just the app image from that commit;
   `postgres`/observability containers are untouched.
3. If the bad deploy already wrote bad data (not just bad code), that's a data rollback, not a
   deploy rollback — see **Restore from backup** above.

**Known gap:** there's no image registry/tagged-release pipeline in this exercise — CI
(`.github/workflows/ci.yml`) builds and validates the image but doesn't push it anywhere, so
"rollback" today means `git checkout` + rebuild, not "redeploy a previous immutable image."
Documented as a Next Step in the README.

## Trace one call end-to-end

The scenario the challenge brief poses directly: *"What happened on the call at 2:47 PM?"*

1. Grafana → Explore → Loki datasource → `{container=~"patient-intake-voice-worker.*"}` for
   the time window. Every log line during a call is already tagged `"room": "<room name>"`
   (`app/worker.py`: `ctx.log_context_fields = {"room": ctx.room.name}`), so once you spot the
   room name for the call in question, narrow with
   `{container=~"patient-intake-voice-worker.*"} | json | room=\"<room>\"`.
2. That single filtered view shows: the STT transcript of everything the caller said, every
   tool call the agent made (`begin_collection`, `submit_identity`, `save_record`, ...) with
   its arguments, any validation errors returned to the model, and — if `save_record` was
   involved — the `record_id` it created/updated (`app/flow.py`'s logging, added specifically
   so a saved record can be traced back to the call that produced it without a schema change).
3. For the REST API side of a request instead: filter
   `{container="patient-intake-voice-api-1"} | json | request_id=\"<id>\"` — the `X-Request-ID`
   response header (`app/obs.py`) is what a staff member/EHR integration should quote back when
   reporting an API issue.
