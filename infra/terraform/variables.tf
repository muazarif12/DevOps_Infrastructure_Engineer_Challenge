variable "project_id" {
  description = "GCP project ID. Assumed to already exist with billing enabled."
  type        = string
}

variable "region" {
  description = "Region for Artifact Registry, Cloud Run, and the GCS backup bucket."
  type        = string
  default     = "us-central1"
}

variable "repo_name" {
  description = "Artifact Registry (Docker) repository name."
  type        = string
  default     = "patient-intake-voice"
}

variable "worker_service_name" {
  type    = string
  default = "patient-intake-worker"
}

variable "api_service_name" {
  type    = string
  default = "patient-intake-api"
}

variable "backup_bucket_name" {
  description = "GCS bucket name for database backups. Bucket names are globally unique across all of GCS — override this."
  type        = string
  default     = "patient-intake-voice-backups"
}

# --- GitHub / Cloud Build trigger -----------------------------------------------------------
# One manual, one-time prerequisite this can't fully automate: the Cloud Build GitHub App must
# be connected to this repo via the GCP Console (Cloud Build → Triggers → Connect Repository)
# before `terraform apply` can create the trigger below — Google doesn't expose that OAuth
# handshake through the provider. See infra/GCP_DEPLOYMENT.md.

variable "github_owner" {
  description = "GitHub username/org that owns the repo (e.g. the \"you\" in github.com/you/repo)."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name (without the owner prefix)."
  type        = string
}

variable "trigger_branch" {
  description = "Branch pattern (regex) that triggers a build/deploy."
  type        = string
  default     = "^main$"
}

# --- LiveKit (required by the worker) -------------------------------------------------------

variable "livekit_url" {
  type      = string
  sensitive = true
}

variable "livekit_api_key" {
  type      = string
  sensitive = true
}

variable "livekit_api_secret" {
  type      = string
  sensitive = true
}

# --- Database — Railway-hosted Postgres, not a GCP-managed database ------------------------
# Cloud Run needs no VPC connector for this: it's a normal outbound internet connection to
# Railway's public Postgres proxy, same as connecting from anywhere else.

variable "railway_pg_host" {
  description = "Host from Railway's Postgres connection details, e.g. containers-us-west-1.railway.app."
  type        = string
  sensitive   = true
}

variable "railway_pg_port" {
  description = "Port from Railway's Postgres connection details — Railway assigns a random external port per-project, it's essentially never 5432."
  type        = string
  sensitive   = true
}

variable "railway_pg_database" {
  type      = string
  default   = "railway"
  sensitive = true
}

variable "railway_pg_user" {
  type      = string
  default   = "postgres"
  sensitive = true
}

variable "railway_pg_password" {
  type      = string
  sensitive = true
}
