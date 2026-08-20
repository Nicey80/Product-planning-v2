resource "random_password" "db" {
  length  = 24
  special = false
}

resource "google_sql_database_instance" "main" {
  project             = var.project_id
  name                = "${local.name_prefix}-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.db_deletion_protection

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_size         = var.db_disk_size_gb
    disk_autoresize   = true
    user_labels       = local.labels

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = var.db_availability_type == "REGIONAL"
    }

    ip_configuration {
      # Cloud Run connects over the Cloud SQL Auth Proxy built into the
      # Cloud Run v2 API via a unix socket (see cloud_run.tf /
      # cloud_run_jobs.tf `volumes.cloud_sql_instance`), authenticated
      # through the Cloud SQL Admin API -- not through this IP. Public IP
      # stays on only so an operator can reach the instance directly
      # (`cloud-sql-proxy` from a laptop, with `gcloud sql connect`'s
      # temporary authorized-network grant) without provisioning a VPC.
      ipv4_enabled = true
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_user" "app" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = "app"
  password = random_password.db.result
}

resource "google_sql_database" "app" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = "subscription_forecasting"
}
