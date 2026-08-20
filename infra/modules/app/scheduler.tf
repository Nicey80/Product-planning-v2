# Non-prod cost control: stop the Cloud SQL instance outside working
# hours and start it again before them, via Cloud Scheduler HTTP targets
# hitting the Cloud SQL Admin API's instance patch endpoint directly
# (activationPolicy NEVER/ALWAYS) -- no Cloud Function or Cloud Run job
# needed for something this small.
resource "google_service_account" "cloudsql_scheduler" {
  count        = var.enable_cloudsql_schedule ? 1 : 0
  project      = var.project_id
  account_id   = "${local.name_prefix}-sql-sched"
  display_name = "Cloud Scheduler: stop/start Cloud SQL (${var.environment})"
}

resource "google_project_iam_member" "cloudsql_scheduler_editor" {
  count   = var.enable_cloudsql_schedule ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.editor"
  member  = "serviceAccount:${google_service_account.cloudsql_scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "cloudsql_stop" {
  count       = var.enable_cloudsql_schedule ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "${local.name_prefix}-sql-stop"
  description = "Stop Cloud SQL outside working hours (non-prod cost control)."
  schedule    = var.cloudsql_stop_cron
  time_zone   = var.cloudsql_schedule_timezone

  http_target {
    http_method = "PATCH"
    uri         = "https://sqladmin.googleapis.com/sql/v1beta4/projects/${var.project_id}/instances/${google_sql_database_instance.main.name}"
    body        = base64encode(jsonencode({ settings = { activationPolicy = "NEVER" } }))
    headers = {
      "Content-Type" = "application/json"
    }
    oauth_token {
      service_account_email = google_service_account.cloudsql_scheduler[0].email
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_scheduler_job" "cloudsql_start" {
  count       = var.enable_cloudsql_schedule ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "${local.name_prefix}-sql-start"
  description = "Start Cloud SQL before working hours (non-prod cost control)."
  schedule    = var.cloudsql_start_cron
  time_zone   = var.cloudsql_schedule_timezone

  http_target {
    http_method = "PATCH"
    uri         = "https://sqladmin.googleapis.com/sql/v1beta4/projects/${var.project_id}/instances/${google_sql_database_instance.main.name}"
    body        = base64encode(jsonencode({ settings = { activationPolicy = "ALWAYS" } }))
    headers = {
      "Content-Type" = "application/json"
    }
    oauth_token {
      service_account_email = google_service_account.cloudsql_scheduler[0].email
    }
  }

  depends_on = [google_project_service.apis]
}
