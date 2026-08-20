output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "frontend_url" {
  value = google_cloud_run_v2_service.frontend.uri
}

output "cloudsql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "artifact_registry_repository" {
  description = "ARTIFACT_REGISTRY_REPO GitHub Environment variable value for this environment."
  value       = google_artifact_registry_repository.app.repository_id
}

output "wif_provider" {
  description = "WIF_PROVIDER GitHub Environment variable value for this environment."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account_email" {
  description = "DEPLOY_SA_EMAIL GitHub Environment variable value for this environment."
  value       = google_service_account.deploy.email
}

output "runtime_service_account_email" {
  value = google_service_account.runtime.email
}

output "bigquery_dataset_id" {
  value = google_bigquery_dataset.warehouse.dataset_id
}
