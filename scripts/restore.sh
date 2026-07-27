#!/usr/bin/env bash
# Restores the most recent backup (or a specific one via --key) into POSTGRES_DB.
#
# The dump was taken with `pg_dump --clean --if-exists` (see backup.sh), so this is safe to run
# against a live database with existing tables — it emits DROP ... IF EXISTS before each CREATE,
# rather than failing on "relation already exists".
#
# In production, stop the api/worker services first (`docker compose stop api worker`) so no
# connections are mutating rows mid-restore; this script does not do that automatically since
# during a restore drill (proving backups work, not responding to an actual incident) you
# normally want the stack left running.
set -euo pipefail

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET must be set}"

key="${1:-}"
if [ -z "$key" ]; then
  echo "[restore] no key given, looking up the latest backup ..."
  key="$(python3 "$(dirname "$0")/s3_object.py" latest-key --bucket "$BACKUP_BUCKET" --prefix backups/)"
fi

dump_file="/tmp/restore-$(basename "$key")"
cleanup() { rm -f "$dump_file"; }
trap cleanup EXIT

echo "[restore] downloading s3://${BACKUP_BUCKET}/${key} ..."
python3 "$(dirname "$0")/s3_object.py" download --bucket "$BACKUP_BUCKET" --key "$key" --file "$dump_file"

echo "[restore] applying to ${POSTGRES_DB}@${POSTGRES_HOST}:${POSTGRES_PORT} ..."
gunzip -c "$dump_file" | PGPASSWORD="$POSTGRES_PASSWORD" psql \
  -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 \
  --quiet

echo "[restore] done: restored from ${key}"
