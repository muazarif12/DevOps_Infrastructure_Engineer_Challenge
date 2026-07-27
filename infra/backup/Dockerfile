# Small, separate image for backup/restore — the app image has no reason to carry postgres
# client tools, and this one has no reason to carry the voice/LLM stack.
FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl gzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir boto3

WORKDIR /app
COPY scripts/backup.sh scripts/restore.sh scripts/s3_object.py ./
RUN chmod +x backup.sh restore.sh

# No CMD: docker-compose.yml supplies the loop for the long-running `backup` service, and
# `docker compose run backup ./restore.sh [key]` is invoked ad hoc for restores/drills.
