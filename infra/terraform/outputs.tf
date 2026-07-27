output "api_url" {
  description = "Public URL of the patient-record REST API."
  value       = google_cloud_run_v2_service.api.uri
}

output "worker_url" {
  description = "Internal-only URL of the worker's health endpoint (not reachable from the public internet — see ingress = INGRESS_TRAFFIC_INTERNAL_ONLY in cloud_run.tf)."
  value       = google_cloud_run_v2_service.worker.uri
}

output "artifact_registry_repo" {
  value = local.artifact_registry_repo
}

output "backup_bucket" {
  value = google_storage_bucket.backups.name
}

output "backup_job_name" {
  value = google_cloud_run_v2_job.backup.name
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "cloudbuild_trigger_id" {
  value = google_cloudbuild_trigger.deploy_on_push.trigger_id
}

# Plain passthroughs of the vars deploy.sh needs to construct the first, manual
# `gcloud builds submit --substitutions=...` call — kept here so that script has one place
# (terraform output) to read them from instead of re-parsing terraform.tfvars itself.
output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "repo_name" {
  value = var.repo_name
}

output "worker_service_name" {
  value = var.worker_service_name
}

output "api_service_name" {
  value = var.api_service_name
}
