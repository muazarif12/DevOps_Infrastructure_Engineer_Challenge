locals {
  artifact_registry_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"

  # Cloud Run needs *some* valid image to create the service with, but the real one only
  # exists after the first Cloud Build run (see cloudbuild.yaml / deploy.sh). Google's own
  # placeholder image bootstraps this; `lifecycle.ignore_changes` below then stops Terraform
  # fighting with Cloud Build over the image tag on every subsequent `terraform apply` — Cloud
  # Build owns the deployed image after the first real deploy, Terraform owns everything else
  # about the service (secrets, scaling, IAM).
  bootstrap_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

# --- Worker: answers phone calls. No public traffic — egress-only to LiveKit Cloud. ---------

resource "google_cloud_run_v2_service" "worker" {
  depends_on = [google_project_service.required]

  name     = var.worker_service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      # Must never scale to zero: this is a persistent LiveKit connection, not a request-driven
      # workload. Capped at 1, not just floored at 1: two instances would both register under
      # the same LiveKit agent name and race for call dispatches against two code paths.
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = local.bootstrap_image

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi" # observed ~2.6GB for the inference process locally; leaves headroom
        }
        # CPU must stay allocated even with no inbound HTTP request in flight — the worker's
        # LiveKit connection and call-handling run in the background, not inside a request.
        cpu_idle = false
      }

      dynamic "env" {
        for_each = toset(["livekit_url", "livekit_api_key", "livekit_api_secret", "intake_db_url"])
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
        name  = "LIVEKIT_LOG_LEVEL"
        value = "INFO"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# --- API: patient-record REST service. Public, request-driven, scales to zero when idle. ----

resource "google_cloud_run_v2_service" "api" {
  depends_on = [google_project_service.required]

  name     = var.api_service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image   = local.bootstrap_image
      command = ["/bin/sh", "-c"]
      # $PORT is Cloud Run's own injected env var (defaults to 8080) — respecting it rather
      # than a hardcoded port is the standard Cloud Run convention.
      args = ["uv run uvicorn app.web:app --host 0.0.0.0 --port $PORT"]

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true # standard request-driven billing; fine to pause CPU between requests
      }

      env {
        name = "INTAKE_DB_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["intake_db_url"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# No X-API-Key requirement (see docs/SECURITY.md — deliberately rolled back to match the app's
# pre-assessment behavior), so this is genuinely open once deployed: anyone with the URL can
# hit it. Matches the Railway deployment's posture; not a Cloud-Run-specific gap.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
