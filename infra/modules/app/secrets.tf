resource "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = "${local.name_prefix}-database-url"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# psycopg's `host=` query param takes the directory holding the unix
# socket (it appends `.s.PGSQL.5432` itself) -- `/cloudsql/<connection
# name>` is exactly where Cloud Run mounts the Cloud SQL Auth Proxy
# socket via the `cloudsql` volume in cloud_run.tf / cloud_run_jobs.tf.
resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = join("", [
    "postgresql+psycopg://",
    google_sql_user.app.name, ":", random_password.db.result,
    "@/", google_sql_database.app.name,
    "?host=/cloudsql/", google_sql_database_instance.main.connection_name,
  ])
}
