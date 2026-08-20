resource "google_artifact_registry_repository" "app" {
  project       = var.project_id
  location      = var.region
  repository_id = "${local.name_prefix}-app"
  format        = "DOCKER"
  description   = "api / frontend images for ${var.environment} (forecast job and db migration reuse the api image)"
  labels        = local.labels

  depends_on = [google_project_service.apis]
}
