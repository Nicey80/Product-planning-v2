project_id        = "CHANGEME-subfx-prod"
region            = "us-central1"
environment       = "prod"
github_repository = "nicey80/product-planning-v2"

# The app enforces its own user auth (see backend/src/backend/db/models
# /auth.py) above whatever Cloud Run's own ingress control provides;
# flip this to false and put Cloud Run behind IAP instead if that stops
# being sufficient.
allow_unauthenticated = true

api_min_instances = 1
api_max_instances = 10
api_cpu           = "2"
api_memory        = "1Gi"

frontend_min_instances = 1
frontend_max_instances = 5
frontend_cpu           = "1"
frontend_memory        = "256Mi"

forecast_job_cpu    = "4"
forecast_job_memory = "4Gi"

db_tier                = "db-custom-2-7680"
db_availability_type   = "REGIONAL"
db_disk_size_gb        = 50
db_deletion_protection = true

# Prod stays up around the clock.
enable_cloudsql_schedule = false

bigquery_dataset_id = "warehouse"
bigquery_location   = "US"
