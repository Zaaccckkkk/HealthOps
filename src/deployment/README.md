# Deployment

Single-GPU development VM on Google Cloud: `cloud/` holds the Compose file, Caddyfile, startup
script and environment templates; `scripts/vm.sh` wraps gcloud (billable actions require
`--yes`) and `scripts/remote.sh` runs on the VM. Procedure, costs and observed numbers are in
[docs/cloud-dev-deployment.md](../../docs/cloud-dev-deployment.md).

The separate synthetic Cloud Run / Cloud SQL deployment is documented in [cloud-run/README.md](cloud-run/README.md).
It uses a dedicated model VM; the previous personal-history database is not part of this environment.
