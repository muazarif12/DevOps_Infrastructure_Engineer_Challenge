#!/usr/bin/env bash
# One script, whole GCP deployment: Terraform provisions everything (Artifact Registry, Secret
# Manager, the worker/api Cloud Run services, the backup Cloud Run Job + Cloud Scheduler
# trigger, the Cloud Build trigger), then this same script does the first real build+deploy
# manually — because the Cloud Build trigger created above only fires on a *future* git push,
# and Cloud Run needs an actual image before it, since it deploys with a placeholder Google
# image the very first time (see cloud_run.tf's `bootstrap_image`).
#
# After this script finishes once, every subsequent `git push` to the trigger branch redeploys
# automatically — this script doesn't need to be re-run for that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

for bin in terraform gcloud docker; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "[deploy] '$bin' not found on PATH — install it first." >&2
    exit 1
  }
done

if [ ! -f terraform.tfvars ]; then
  echo "[deploy] terraform.tfvars not found." >&2
  echo "         Copy terraform.tfvars.example -> terraform.tfvars and fill in real values first." >&2
  exit 1
fi

echo "[deploy] checking gcloud auth ..."
gcloud auth print-access-token >/dev/null 2>&1 || {
  echo "[deploy] not logged in — run: gcloud auth login" >&2
  exit 1
}

echo "[deploy] terraform init ..."
terraform init

echo "[deploy] terraform apply ..."
echo "         Creates: Artifact Registry repo, Secret Manager secrets, the worker + api Cloud"
echo "         Run services (with a placeholder image for now), the backup Cloud Run Job +"
echo "         Cloud Scheduler trigger, and the Cloud Build trigger for future pushes."
echo "         Requires: the Cloud Build GitHub App already connected to this repo once via"
echo "         GCP Console (see infra/GCP_DEPLOYMENT.md) — 'terraform apply' fails at the"
echo "         trigger resource otherwise."
terraform apply

PROJECT_ID="$(terraform output -raw project_id)"
REGION="$(terraform output -raw region)"
REPO="$(terraform output -raw repo_name)"
WORKER_SERVICE="$(terraform output -raw worker_service_name)"
API_SERVICE="$(terraform output -raw api_service_name)"
BACKUP_JOB="$(terraform output -raw backup_job_name)"

echo "[deploy] first real build + deploy (gcloud builds submit) ..."
echo "         Runs the exact same infra/terraform/cloudbuild.yaml the trigger will use on"
echo "         every future push — this call just does it once, right now, manually."
gcloud builds submit \
  --project="$PROJECT_ID" \
  --config="$SCRIPT_DIR/cloudbuild.yaml" \
  --substitutions="_REGION=${REGION},_REPO=${REPO},_WORKER_SERVICE=${WORKER_SERVICE},_API_SERVICE=${API_SERVICE},_BACKUP_JOB=${BACKUP_JOB}" \
  "$REPO_ROOT"

echo "[deploy] done."
echo "[deploy] api:    $(terraform output -raw api_url)"
echo "[deploy] worker: $(terraform output -raw worker_url) (internal-only — see cloud_run.tf)"
echo "[deploy] From now on, every push to the trigger branch redeploys automatically."
