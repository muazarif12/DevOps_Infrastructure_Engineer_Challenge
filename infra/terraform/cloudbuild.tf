# One manual prerequisite this can't automate away: before this trigger will actually fire,
# the Cloud Build GitHub App must be connected to this repo once via GCP Console
# (Cloud Build → Triggers → Connect Repository → GitHub). That OAuth handshake isn't exposed
# through the Terraform provider. See infra/GCP_DEPLOYMENT.md.
resource "google_cloudbuild_trigger" "deploy_on_push" {
  depends_on = [google_project_service.required]

  name        = "patient-intake-deploy"
  description = "Build worker/api/backup images and deploy them on every push to ${var.trigger_branch}"
  location    = "global"

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = var.trigger_branch
    }
  }

  filename = "infra/terraform/cloudbuild.yaml"

  substitutions = {
    _REGION         = var.region
    _REPO           = var.repo_name
    _WORKER_SERVICE = var.worker_service_name
    _API_SERVICE    = var.api_service_name
    _BACKUP_JOB     = google_cloud_run_v2_job.backup.name
  }
}
