# GCS bucket for database backups — the actual storage target for scripts/backup.sh and
# restore.sh, unchanged from the Compose/MinIO version of those same scripts. GCS exposes an
# S3-compatible XML API, so the existing boto3-based scripts/s3_object.py talks to it directly
# via AWS_ENDPOINT_URL=https://storage.googleapis.com — no new backup code, no new Python
# dependency, just a different endpoint and a GCS-issued HMAC key pair standing in for AWS
# credentials.

resource "google_storage_bucket" "backups" {
  depends_on = [google_project_service.required]

  name     = var.backup_bucket_name
  location = var.region

  # Backups aren't public data by nature; deny anything but authenticated access via the
  # runtime service account below.
  public_access_prevention = "enforced"
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "runtime_backup_access" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# HMAC key so the runtime service account can authenticate to GCS's S3-compatible endpoint the
# same way it would authenticate to real AWS S3 or MinIO — access_id/secret below are wired
# into Secret Manager (see secrets.tf) as AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.
resource "google_storage_hmac_key" "runtime" {
  service_account_email = google_service_account.runtime.email
}
