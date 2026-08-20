# infra/

Terraform for the subscription forecasting app's GCP infrastructure: one
module (`modules/app`), applied once per environment with that
environment's tfvars. Each environment is a **separate GCP project** --
dev, staging, and prod never share billing, IAM, or a Cloud SQL instance.

```
infra/
  main.tf, variables.tf, outputs.tf, versions.tf   root config: calls modules/app once
  environments/
    dev.tfvars        staging.tfvars        prod.tfvars
    dev.backend.tfvars staging.backend.tfvars prod.backend.tfvars
  modules/app/         the one module: Cloud Run (api, frontend, forecast
                        job, migrate job), Cloud SQL, BigQuery, Artifact
                        Registry, Secret Manager, Workload Identity
                        Federation, Cloud Scheduler
```

## What it creates, per environment

- **Artifact Registry** -- one Docker repo holding the `api` and
  `frontend` images (the forecast job and the db-migration job reuse the
  `api` image with a different container command).
- **Cloud SQL for Postgres 16** -- `ZONAL`/minimum tier for dev and
  staging, `REGIONAL`/HA for prod (`db_availability_type`,
  `db_tier` in tfvars). Non-prod also gets a Cloud Scheduler stop/start
  pair (`enable_cloudsql_schedule`) that pauses the instance outside
  working hours.
- **BigQuery dataset** -- the target `warehouse/` (dbt) writes to in this
  environment; matches the `dataset` in
  `warehouse/profiles.yml.example`'s `ci`/`prod` targets.
- **Cloud Run services** -- `api` and `frontend`, scale-to-zero by
  default on dev/staging (`*_min_instances = 0`).
- **Cloud Run Jobs** -- `forecast` (runs
  `backend.jobs.run_forecast`, see CLAUDE.md "run lineage") and
  `migrate` (`alembic upgrade head`, executed by deploy-env.yml before
  every staging/prod deploy).
- **Secret Manager** -- `DATABASE_URL`, built from a Terraform-generated
  password and granted to the runtime service account only.
- **Workload Identity Federation** -- a pool + OIDC provider trusting
  only `assertion.repository == "<github_repository>"`, plus a deploy
  service account GitHub Actions assumes through it. **No service
  account key exists anywhere** for any environment.

## Bootstrap (once per environment)

1. Create the GCP project (or have one created for you) and note its
   project ID.
2. Create the Terraform state bucket for that environment (versioned, so
   a bad apply's state is recoverable):
   ```sh
   gcloud storage buckets create gs://<project-id>-tfstate \
     --project=<project-id> --location=us-central1 --uniform-bucket-level-access
   gcloud storage buckets update gs://<project-id>-tfstate --versioning
   ```
3. Fill in the `CHANGEME` values in `environments/<env>.tfvars` and
   `environments/<env>.backend.tfvars` (project ID and state bucket name
   from steps 1-2).
4. Authenticate locally as a principal with Owner (or a tight
   equivalent) on the project, then:
   ```sh
   cd infra
   terraform init -backend-config=environments/dev.backend.tfvars -reconfigure
   terraform apply -var-file=environments/dev.tfvars
   ```
   Repeat with `staging`/`prod` in their own project, re-running `init
   -reconfigure` to switch state backends between environments (or use
   separate working directories / `terraform workspace` if you'd rather
   not re-init).
5. Wire the outputs into GitHub: create a **GitHub Environment** named
   `preview`, `staging`, or `production` matching what `deploy.yml` /
   `deploy-env.yml` target (dev's apply feeds the `preview` environment;
   staging's and prod's feed their own). In each environment's
   Settings -> Environments -> Variables, set:

   | Variable                 | From                                     |
   | ------------------------- | ----------------------------------------- |
   | `GCP_PROJECT`              | the environment's `project_id`            |
   | `GCP_REGION`                | `terraform output -raw` region (tfvars' `region`) |
   | `ARTIFACT_REGISTRY_REPO`     | `terraform output -raw artifact_registry_repository` |
   | `WIF_PROVIDER`               | `terraform output -raw wif_provider`      |
   | `DEPLOY_SA_EMAIL`             | `terraform output -raw deploy_service_account_email` |

   For `production` **only**, also add a required reviewer under
   Settings -> Environments -> production -> "Deployment protection
   rules" -- that's what makes `deploy.yml`'s prod job pause for manual
   approval on a tag push. Don't add that rule to `preview` or
   `staging`; they're meant to deploy automatically.

6. First deploy: push to `main` (stages `staging`) or push a `v*` tag
   (stages `production`, pending approval) -- see the root README and
   `.github/workflows/deploy.yml`. This replaces the bootstrap
   placeholder image (`us-docker.pkg.dev/cloudrun/container/hello`) with
   a real one; `terraform apply` never does this itself (see the
   `lifecycle.ignore_changes` note in `modules/app/cloud_run.tf`).

## Everyday use

```sh
cd infra
terraform init -backend-config=environments/<env>.backend.tfvars -reconfigure
terraform plan  -var-file=environments/<env>.tfvars
terraform apply -var-file=environments/<env>.tfvars
```

Terraform owns each service/job's *shape* (scaling, resources, identity,
volumes, secrets) -- not its running image or traffic split. Those are
deploy-env.yml's job (`gcloud run deploy --image=... --tag=...`), and are
explicitly excluded from Terraform's plan via `lifecycle.ignore_changes`
so routine deploys and routine `terraform apply` runs never fight each
other.

## Notes and deliberate simplifications

- **No VPC / private IP.** Cloud Run reaches Cloud SQL through the Cloud
  SQL Auth Proxy built into the Cloud Run v2 API (a `cloud_sql_instance`
  volume, authenticated via the Cloud SQL Admin API) rather than a VPC
  connector -- there's no private networking to provision. The
  instance's public IP exists only for an operator to reach it directly
  when needed (e.g. `gcloud sql connect`); it has no standing authorized
  networks.
- **`allow_unauthenticated = true`** on `api`/`frontend` in every
  environment, including prod. The application has its own user/role/
  scope model (`backend/src/backend/db/models/auth.py`) enforcing access
  above the HTTP layer; if that stops being sufficient, flip this to
  `false` and put the services behind Identity-Aware Proxy instead.
- **Local validation:** this module was formatted with `terraform fmt`
  and reviewed by hand; `terraform validate`/`plan` need registry access
  this sandbox didn't have -- run both before your first real `apply`.
