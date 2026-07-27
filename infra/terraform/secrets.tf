# Every credential the app needs, as Secret Manager secrets — set on the Cloud Run services as
# real secret references (cloud_run.tf), never as plain env vars, and never committed anywhere:
# values come in only through Terraform variables (terraform.tfvars, gitignored).

locals {
  # Same shape as INTAKE_DB_URL locally/on Railway — postgresql+psycopg:// is required because
  # only psycopg (v3) is installed, not psycopg2 (see app/store.py's _normalize_db_url, which
  # would also fix a bare postgresql:// here, but building it correctly up front avoids relying
  # on that fallback).
  intake_db_url = "postgresql+psycopg://${var.railway_pg_user}:${var.railway_pg_password}@${var.railway_pg_host}:${var.railway_pg_port}/${var.railway_pg_database}"

  app_secrets = {
    livekit_url            = var.livekit_url
    livekit_api_key        = var.livekit_api_key
    livekit_api_secret     = var.livekit_api_secret
    intake_db_url          = local.intake_db_url
    # Separate components, not just the combined URL above — scripts/backup.sh (running in the
    # Cloud Run Job in backup.tf) invokes pg_dump/psql directly, which take these individually.
    postgres_host          = var.railway_pg_host
    postgres_port          = var.railway_pg_port
    postgres_db            = var.railway_pg_database
    postgres_user          = var.railway_pg_user
    postgres_password      = var.railway_pg_password
    # GCS's S3-compatible endpoint credentials for scripts/backup.sh + restore.sh — same HMAC
    # key created in storage.tf, standing in for AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.
    aws_access_key_id      = google_storage_hmac_key.runtime.access_id
    aws_secret_access_key  = google_storage_hmac_key.runtime.secret
  }
}

resource "google_secret_manager_secret" "app" {
  for_each = local.app_secrets

  depends_on = [google_project_service.required]
  secret_id  = "patient-intake-${replace(each.key, "_", "-")}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "app" {
  for_each = local.app_secrets

  secret      = google_secret_manager_secret.app[each.key].id
  secret_data = each.value
}
