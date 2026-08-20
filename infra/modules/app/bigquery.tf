resource "google_bigquery_dataset" "warehouse" {
  project    = var.project_id
  dataset_id = var.bigquery_dataset_id
  location   = var.bigquery_location
  labels     = local.labels

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_dataset_iam_member" "runtime_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}
