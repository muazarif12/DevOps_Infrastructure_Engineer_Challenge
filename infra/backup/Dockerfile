# Small, separate image for backup/restore — the app image has no reason to carry postgres
# client tools, and this one has no reason to carry the voice/LLM stack. Built by
# infra/terraform/cloudbuild.yaml and run as a Cloud Run Job (see infra/terraform/backup.tf).
FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl gzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir boto3

WORKDIR /app
COPY scripts/backup.sh scripts/restore.sh scripts/s3_object.py ./
RUN chmod +x backup.sh restore.sh

# No CMD: the Cloud Run Job (infra/terraform/backup.tf) sets `command = ["./backup.sh"]`
# explicitly; `./restore.sh [key]` is invoked ad hoc for restores/drills via
# `gcloud run jobs execute <job> --command=./restore.sh --args=<key> --region=...`.
