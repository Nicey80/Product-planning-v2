variable "project_id" {
  description = "GCP project this environment's resources live in. Each environment (dev/staging/prod) is a separate project."
  type        = string
}

variable "region" {
  description = "Primary GCP region for all resources."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = <<-EOT
    Environment name: dev, staging, or prod. Drives non-prod cost-saving
    defaults (scale-to-zero, minimum tiers, scheduled Cloud SQL
    stop/start) and prod-only protections (deletion protection, regional
    HA) via the tfvars files in infra/environments/, not by branching in
    this module -- the module itself stays a single, uniform shape.
  EOT
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "github_repository" {
  description = "GitHub \"owner/repo\" that Workload Identity Federation trusts for deploys. No other repo, and no fork of this repo, can assume the deploy service account."
  type        = string
}

# --- Container images -------------------------------------------------------
# Bootstrapped to a public placeholder so the first `terraform apply`
# succeeds before CI has ever pushed a real image; deploy-env.yml then
# updates the running revision directly via `gcloud run deploy --image=`.
# lifecycle.ignore_changes on each image field (cloud_run.tf,
# cloud_run_jobs.tf) stops a later `terraform apply` from reverting that
# back to the placeholder.
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

# --- Cloud Run: api service --------------------------------------------------
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

# --- Cloud Run: frontend service ---------------------------------------------
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
  description = "Allow public (unauthenticated) invocation of the api/frontend Cloud Run services. The application enforces its own user auth (see backend/src/backend/db/models/auth.py) above this."
  type        = bool
  default     = true
}

# --- Cloud Run Jobs: forecast execution + db migration -----------------------
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
  description = "Forecast job task timeout, as a duration string (e.g. \"3600s\")."
  type        = string
  default     = "3600s"
}

# --- Cloud SQL -----------------------------------------------------------------
variable "db_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "db_availability_type" {
  description = "ZONAL (non-prod, cheaper, no HA) or REGIONAL (prod, HA)."
  type        = string
  default     = "ZONAL"
  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.db_availability_type)
    error_message = "db_availability_type must be ZONAL or REGIONAL."
  }
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
  description = "Stop the Cloud SQL instance outside working hours via Cloud Scheduler (non-prod only -- prod must stay up)."
  type        = bool
  default     = false
}

variable "cloudsql_stop_cron" {
  description = "Cron schedule (in cloudsql_schedule_timezone) to stop the Cloud SQL instance."
  type        = string
  default     = "0 20 * * 1-5"
}

variable "cloudsql_start_cron" {
  description = "Cron schedule (in cloudsql_schedule_timezone) to start the Cloud SQL instance."
  type        = string
  default     = "0 7 * * 1-5"
}

variable "cloudsql_schedule_timezone" {
  type    = string
  default = "Etc/UTC"
}

# --- BigQuery --------------------------------------------------------------------
variable "bigquery_dataset_id" {
  description = "Matches the `dataset` in warehouse/profiles.yml.example's ci/prod targets for this environment."
  type        = string
}

variable "bigquery_location" {
  type    = string
  default = "US"
}

# --- Observability -----------------------------------------------------------------
variable "otel_collector_endpoint" {
  description = "OTLP/gRPC endpoint traces are exported to (OTEL_EXPORTER_OTLP_ENDPOINT). Empty disables export -- spans are still created, just never shipped."
  type        = string
  default     = ""
}
