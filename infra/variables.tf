# Mirrors infra/modules/app/variables.tf 1:1 -- this root config is a
# thin pass-through to the one module, per environment, via
# infra/environments/<env>.tfvars. See infra/README.md.

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "github_repository" {
  type = string
}

variable "api_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "frontend_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "forecast_job_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "api_min_instances" {
  type    = number
  default = 0
}

variable "api_max_instances" {
  type    = number
  default = 3
}

variable "api_cpu" {
  type    = string
  default = "1"
}

variable "api_memory" {
  type    = string
  default = "512Mi"
}

variable "frontend_min_instances" {
  type    = number
  default = 0
}

variable "frontend_max_instances" {
  type    = number
  default = 2
}

variable "frontend_cpu" {
  type    = string
  default = "1"
}

variable "frontend_memory" {
  type    = string
  default = "256Mi"
}

variable "allow_unauthenticated" {
  type    = bool
  default = true
}

variable "forecast_job_cpu" {
  type    = string
  default = "2"
}

variable "forecast_job_memory" {
  type    = string
  default = "2Gi"
}

variable "forecast_job_max_retries" {
  type    = number
  default = 1
}

variable "forecast_job_task_timeout" {
  type    = string
  default = "3600s"
}

variable "db_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "db_availability_type" {
  type    = string
  default = "ZONAL"
}

variable "db_disk_size_gb" {
  type    = number
  default = 10
}

variable "db_deletion_protection" {
  type    = bool
  default = false
}

variable "enable_cloudsql_schedule" {
  type    = bool
  default = false
}

variable "cloudsql_stop_cron" {
  type    = string
  default = "0 20 * * 1-5"
}

variable "cloudsql_start_cron" {
  type    = string
  default = "0 7 * * 1-5"
}

variable "cloudsql_schedule_timezone" {
  type    = string
  default = "Etc/UTC"
}

variable "bigquery_dataset_id" {
  type = string
}

variable "bigquery_location" {
  type    = string
  default = "US"
}

variable "otel_collector_endpoint" {
  type    = string
  default = ""
}
