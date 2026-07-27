#!/usr/bin/env bash
# Dumps Postgres, gzips it, and uploads it to an S3-compatible bucket (MinIO, real S3, or GCS's
# S3-compatible endpoint — see scripts/s3_object.py). If PUSHGATEWAY_URL is set, also pushes a
# heartbeat timestamp there so a Prometheus alert_rules.yml-style BackupStale rule can catch a
# backup job that silently stopped running — but this is opportunistic, not required: on
# deployments with no Prometheus/Pushgateway (e.g. the GCP Cloud Run Job in
# infra/terraform/backup.tf), PUSHGATEWAY_URL is simply left unset and this step is skipped
# entirely, rather than failing an otherwise-successful backup over an unrelated telemetry call.
#
# Invoked as a Cloud Run Job on a Cloud Scheduler cron (infra/terraform/backup.tf) or a
# long-running loop (see the old docker-compose.yml `backup` service in git history) —
# either way, this script itself does exactly one dump-and-upload per invocation.
set -euo pipefail

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET must be set}"
: "${PUSHGATEWAY_URL:=}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_file="/tmp/backup-${timestamp}.sql.gz"
key="backups/${timestamp}.sql.gz"

cleanup() { rm -f "$dump_file"; }
trap cleanup EXIT

echo "[backup] dumping ${POSTGRES_DB}@${POSTGRES_HOST}:${POSTGRES_PORT} ..."
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --no-owner --no-privileges --clean --if-exists \
  | gzip > "$dump_file"

size=$(stat -c%s "$dump_file" 2>/dev/null || stat -f%z "$dump_file")
if [ "$size" -lt 100 ]; then
  echo "[backup] FAILED: dump suspiciously small (${size} bytes) — refusing to upload" >&2
  exit 1
fi

echo "[backup] uploading to s3://${BACKUP_BUCKET}/${key} (${size} bytes) ..."
python3 "$(dirname "$0")/s3_object.py" upload --bucket "$BACKUP_BUCKET" --key "$key" --file "$dump_file"

if [ -n "$PUSHGATEWAY_URL" ]; then
  echo "[backup] pushing heartbeat to ${PUSHGATEWAY_URL} ..."
  cat <<EOF | curl -sf --data-binary @- "${PUSHGATEWAY_URL}/metrics/job/backup" \
    || echo "[backup] warning: heartbeat push failed, continuing anyway (backup itself already succeeded)" >&2
# TYPE backup_last_success_timestamp_seconds gauge
backup_last_success_timestamp_seconds $(date +%s)
# TYPE backup_last_size_bytes gauge
backup_last_size_bytes ${size}
EOF
fi

echo "[backup] done: ${key}"
