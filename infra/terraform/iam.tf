# One shared service account for everything that runs app code (worker, api, the backup job) —
# simplest option, at the cost of each of those three having access to all of this app's
# secrets and the whole backup bucket rather than a tighter per-service scope. Fine for a
# single-app project; split into per-service accounts first if this project ever hosts more
# than one app.
resource "google_service_account" "runtime" {
  account_id   = "patient-intake-runtime"
  display_name = "Patient intake voice agent — Cloud Run + backup job runtime identity"
}

# Project-level secretAccessor rather than a binding per secret: simpler, and there's nothing
# else in this project for it to over-reach into. Tighten to per-secret google_secret_manager_
# secret_iam_member bindings if that stops being true.
resource "google_project_iam_member" "runtime_secret_access" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# --- Cloud Build's default service account needs to be able to deploy what it builds --------

resource "google_project_iam_member" "cloudbuild_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

# Lets Cloud Build's deploy step "act as" the runtime service account when it points Cloud Run
# at it — without this, `gcloud run deploy --service-account=...` from cloudbuild.yaml fails
# with a permission error.
resource "google_service_account_iam_member" "cloudbuild_actas_runtime" {
  service_account_id = google_service_account.runtime.name
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

