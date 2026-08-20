module "app" {
  source = "./modules/app"

  project_id        = var.project_id
  region            = var.region
  environment       = var.environment
  github_repository = var.github_repository

  api_image          = var.api_image
  frontend_image     = var.frontend_image
  forecast_job_image = var.forecast_job_image

  api_min_instances = var.api_min_instances
  api_max_instances = var.api_max_instances
  api_cpu           = var.api_cpu
  api_memory        = var.api_memory

  frontend_min_instances = var.frontend_min_instances
  frontend_max_instances = var.frontend_max_instances
  frontend_cpu           = var.frontend_cpu
  frontend_memory        = var.frontend_memory

  allow_unauthenticated = var.allow_unauthenticated

  forecast_job_cpu          = var.forecast_job_cpu
  forecast_job_memory       = var.forecast_job_memory
  forecast_job_max_retries  = var.forecast_job_max_retries
  forecast_job_task_timeout = var.forecast_job_task_timeout

  db_tier                = var.db_tier
  db_availability_type   = var.db_availability_type
  db_disk_size_gb        = var.db_disk_size_gb
  db_deletion_protection = var.db_deletion_protection

  enable_cloudsql_schedule   = var.enable_cloudsql_schedule
  cloudsql_stop_cron         = var.cloudsql_stop_cron
  cloudsql_start_cron        = var.cloudsql_start_cron
  cloudsql_schedule_timezone = var.cloudsql_schedule_timezone

  bigquery_dataset_id = var.bigquery_dataset_id
  bigquery_location   = var.bigquery_location

  otel_collector_endpoint = var.otel_collector_endpoint
}
