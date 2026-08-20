resource "google_cloud_run_v2_job" "forecast" {
  project  = var.project_id
  name     = "forecast"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = var.forecast_job_max_retries
      timeout         = var.forecast_job_task_timeout

      containers {
        image   = var.forecast_job_image
        command = ["python", "-m", "backend.jobs.run_forecast"]

        resources {
          limits = {
            cpu    = var.forecast_job_cpu
            memory = var.forecast_job_memory
          }
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
        env {
          name  = "ENVIRONMENT"
          value = var.environment
        }
        dynamic "env" {
          for_each = var.otel_collector_endpoint == "" ? [] : [var.otel_collector_endpoint]
          content {
            name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
            value = env.value
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.main.connection_name]
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      template[0].template[0].containers[0].env,
    ]
  }

  depends_on = [google_project_service.apis]
}

# Runs `alembic upgrade head` against the api image -- executed by
# deploy-env.yml (`gcloud run jobs execute migrate --wait`) before every
# staging/prod deploy, so the schema is never behind the code that
# expects it.
resource "google_cloud_run_v2_job" "migrate" {
  project  = var.project_id
  name     = "migrate"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 0
      timeout         = "300s"

      containers {
        image   = var.api_image
        command = ["alembic", "upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.main.connection_name]
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }

  depends_on = [google_project_service.apis]
}
