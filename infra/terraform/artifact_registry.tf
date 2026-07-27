resource "google_artifact_registry_repository" "app" {
  depends_on = [google_project_service.required]

  location      = var.region
  repository_id = var.repo_name
  format        = "DOCKER"
  description   = "Patient intake voice agent images — the same Dockerfile builds the worker and api services (see docker-compose.yml for the local equivalent), plus infra/backup/Dockerfile for the backup job."
}
