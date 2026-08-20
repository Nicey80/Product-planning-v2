output "api_url" {
  value = module.app.api_url
}

output "frontend_url" {
  value = module.app.frontend_url
}

output "cloudsql_connection_name" {
  value = module.app.cloudsql_connection_name
}

output "artifact_registry_repository" {
  value = module.app.artifact_registry_repository
}

output "wif_provider" {
  value = module.app.wif_provider
}

output "deploy_service_account_email" {
  value = module.app.deploy_service_account_email
}

output "runtime_service_account_email" {
  value = module.app.runtime_service_account_email
}

output "bigquery_dataset_id" {
  value = module.app.bigquery_dataset_id
}
