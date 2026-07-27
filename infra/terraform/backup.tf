# Runs scripts/backup.sh (via infra/backup/Dockerfile — the same image used by docker-compose's
# `backup` service) on a schedule instead of a long-running loop, since Cloud Run Jobs bill only
# for the seconds a run actually takes rather than an always-on container.

resource "google_cloud_run_v2_job" "backup" {
  depends_on = [google_project_service.required]

  name     = "patient-intake-backup"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 1
      timeout         = "600s"

      containers {
        image   = local.bootstrap_image # replaced by the first Cloud Build run, same as the app services
        command = ["./backup.sh"]

        dynamic "env" {
          for_each = toset([
            "postgres_host", "postgres_port", "postgres_db", "postgres_user",
            "postgres_password", "aws_access_key_id", "aws_secret_access_key",
          ])
          content {
            name = upper(env.value)
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.app[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        env {
          name  = "BACKUP_BUCKET"
          value = google_storage_bucket.backups.name
        }
        env {
          name  = "AWS_ENDPOINT_URL"
          value = "https://storage.googleapis.com"
        }
        env {
          name  = "AWS_DEFAULT_REGION" # required by boto3's SigV4 signer; GCS ignores the value
          value = "auto"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

# --- Run it every 15 minutes, matching the docker-compose backup loop's interval ------------

resource "google_cloud_run_v2_job_iam_member" "scheduler_can_run" {
  name     = google_cloud_run_v2_job.backup.name
  location = google_cloud_run_v2_job.backup.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_scheduler_job" "backup" {
  depends_on = [google_project_service.required]

  name             = "patient-intake-backup-trigger"
  region           = var.region
  schedule         = "*/15 * * * *"
  attempt_deadline = "600s"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.backup.name}:run"

    oauth_token {
      service_account_email = google_service_account.runtime.email
    }
  }
}
