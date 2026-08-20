project_id        = "CHANGEME-subfx-staging"
region            = "us-central1"
environment       = "staging"
github_repository = "nicey80/product-planning-v2"

allow_unauthenticated = true

api_min_instances = 0
api_max_instances = 3
api_cpu           = "1"
api_memory        = "512Mi"

frontend_min_instances = 0
frontend_max_instances = 2
frontend_cpu           = "1"
frontend_memory        = "256Mi"

forecast_job_cpu    = "2"
forecast_job_memory = "2Gi"

# Minimum tier, no HA, scale to zero: staging mirrors prod's shape, not
# its capacity.
db_tier                = "db-f1-micro"
db_availability_type   = "ZONAL"
db_disk_size_gb        = 10
db_deletion_protection = false

enable_cloudsql_schedule   = true
cloudsql_stop_cron         = "0 20 * * 1-5"
cloudsql_start_cron        = "0 7 * * 1-5"
cloudsql_schedule_timezone = "America/New_York"

bigquery_dataset_id = "warehouse_staging"
bigquery_location   = "US"
