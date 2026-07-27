#!/usr/bin/env bash
# Dumps Postgres, gzips it, uploads to S3 (MinIO locally — see .env.example / docker-compose.yml),
# and on success pushes a heartbeat timestamp to Pushgateway. Prometheus scrapes that heartbeat
# and alert_rules.yml's BackupStale rule fires if it goes >90 minutes without updating — a backup
# job that silently stops running is worse than no backup job, because nobody notices until the
# restore that needs it fails too.
#
# Run on a loop by the `backup` service in docker-compose.yml (see BACKUP_INTERVAL_SECONDS
# there). A real deployment would use a systemd timer / cron / cloud-scheduled job instead of a
# long-running loop — documented as a next step in README.md.
set -euo pipefail

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET must be set}"
: "${PUSHGATEWAY_URL:=http://pushgateway:9091}"

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

echo "[backup] pushing heartbeat to ${PUSHGATEWAY_URL} ..."
cat <<EOF | curl -sf --data-binary @- "${PUSHGATEWAY_URL}/metrics/job/backup"
# TYPE backup_last_success_timestamp_seconds gauge
backup_last_success_timestamp_seconds $(date +%s)
# TYPE backup_last_size_bytes gauge
backup_last_size_bytes ${size}
EOF

echo "[backup] done: ${key}"
