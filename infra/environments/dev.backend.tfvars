# terraform init -backend-config=environments/dev.backend.tfvars
# The bucket must exist before `init` -- see infra/README.md "Bootstrap".
bucket = "CHANGEME-subfx-dev-tfstate"
prefix = "product-planning-v2"
