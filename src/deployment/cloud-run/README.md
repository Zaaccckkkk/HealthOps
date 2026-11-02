# Cloud Run API with a separate synthetic Cloud SQL database

This runbook records the upstream development deployment in GCP `us-east4`; it is not a
live public demo. Project IDs and service URLs below are placeholders. Configure your own
project, credentials and resource names before running any command. That environment contained only
three synthetic accounts; the previous VM's personal-history database was not copied or queried.
See [deployment evidence](../../../docs/evidence/cloud-run-synthetic.md) for observed checks and limits.

## Architecture

```text
Authorized operator / client
  -> HTTPS + Cloud Run IAM identity + application account token
  -> Cloud Run CPU API (healthops-ms3-api)
       -> private TLS -> Cloud SQL PostgreSQL 17 + pgvector
       -> private HTTP -> Ollama on a dedicated L4 GPU VM
```

Cloud Run executes the existing FastAPI application, including tools and the assistant loop.
Cloud SQL is a separate managed database service, not a database inside Cloud Run. The model VM
stores model weights; health observations and reference chunks live in Cloud SQL. The application
and database are independently available when the model VM is stopped, but assistant requests
cannot complete inference then. Readiness checks the database, not model availability.

Provisioned resources:

| Resource | Configuration |
| --- | --- |
| Project | `your-gcp-project-id` |
| API | `healthops-ms3-api`, `us-east4`, 1 CPU / 1 GiB, min 0 / max 1, concurrency 4 |
| Database instance | `healthops-ms3-synthetic`, PostgreSQL 17 Enterprise, `db-g1-small`, single zone, 10 GB SSD |
| Database | `healthops_synthetic`, private IP only, TLS required by client URL |
| Model VM | `healthops-ms3-model`, `us-east4-c`, `g2-standard-4`, one NVIDIA L4, 100 GB boot disk |
| Model endpoint | `10.150.0.3:11434`, bound only to the VM private interface |
| VPC | `healthops-net`; Cloud Run subnet `healthops-run` (`10.20.0.0/26`) |
| Private service access | `healthops-sql-range`, Service Networking peering |
| Registry | `us-east4-docker.pkg.dev/your-gcp-project-id/healthops` |
| Runtime identity | `healthops-run`; secret access on its database secret only |
| Build identity | `healthops-build`; registry writer, build-source reader, log writer |
| Secret | `healthops-ms3-database-url`, pinned version `1` |

The old `healthops-gpu` in `us-east4-a` could not start because that zone lacked L4 capacity.
It remains stopped. The new VM uses a clean Deep Learning VM image, not a copy of the old disk.
Both the new and old disks remain billable while their VMs are stopped. The new VM has a two-hour
maximum run duration with termination action STOP. Starting it again can still fail due to capacity.
Cloud SQL continues running and billing independently; API scale-to-zero is not a total cost cap.

### Temporary V100 evaluation fallback

V100 is an additional evaluation candidate, not a replacement for the persistent L4 VM.
Check both regional `NVIDIA_V100_GPUS` and global `GPUS_ALL_REGIONS` quotas, then list
`nvidia-tesla-v100` accelerator types to find supported zones. A supported zone and positive
quota do not guarantee allocation. Use one GPU, a 7,200-second automatic STOP, and delete the
temporary VM and its auto-delete boot disk after testing. STOP alone does not remove disk costs.

On 2026-11-02 UTC, `n1-standard-4` with one V100 allocated in `us-central1-a` after two
configuration-availability failures in `us-east1`. The clean DLVM image
`common-cu129-ubuntu-2204-nvidia-580-v20261029` booted with an incompatible open NVIDIA kernel
module. V100 needs the proprietary module. For that exact image/kernel, the observed repair was:

```bash
# On the disposable evaluation VM only; verify the running kernel and package candidates first.
uname -r
sudo apt-get install linux-modules-nvidia-580-server-6.8.0-1070-gcp
sudo apt-get remove linux-modules-nvidia-580-server-open-gcp \
  linux-modules-nvidia-580-server-open-6.8.0-1070-gcp
sudo depmod -a
sudo modprobe nvidia
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
```

The repaired host reported `Tesla V100-SXM2-16GB`, driver `580.178.04`, and 16,384 MiB.
Do not copy these kernel-specific package names to a different image without checking them.
Only after host GPU detection succeeds, use `bootstrap-model.sh` and `compose.model.yml`.
Ollama's automatic discovery initially fell back to CPU on this host. The bundled CUDA 12
backend detected V100 when explicitly selected. Apply the checked-in override on the VM:

```bash
sudo env MODEL_PRIVATE_IP=YOUR_PRIVATE_IP docker compose \
  -f compose.model.yml -f compose.v100.yml up -d
```

Keep Ollama 0.35.1, compare both model digests to the frozen baseline, and confirm GPU offload
and effective context size before grading answers. A 16 GB V100 has less memory than a 24 GB L4;
do not silently change quantization or context to obtain a passing result.

Use a tagged, zero-default-traffic API revision pointing to the temporary VM's private address.
Keep synthetic database identity checks and the existing evaluation rubric. Never reuse the
personal-history VM or copy its disk. Record hardware differences when interpreting latency.

## Build and redeploy

`cloudbuild.yaml` builds from the API service directory, publishes the image and writes Cloud Logging
logs. `.gcloudignore` excludes `.local`, `.env`, virtual environments and caches from the source upload.
The runtime image is unchanged; deployment scripts do not change assistant behavior.

```bash
gcloud builds submit src/api-service \
  --config=src/deployment/cloud-run/cloudbuild.yaml \
  --substitutions=_IMAGE=us-east4-docker.pkg.dev/your-gcp-project-id/healthops/api:YOUR_VERSION \
  --service-account=projects/your-gcp-project-id/serviceAccounts/healthops-build@your-gcp-project-id.iam.gserviceaccount.com \
  --project=your-gcp-project-id
```

Copy `config.example.json` to ignored `.local/cloud/run.json` and configure the actual resource
names, private model address and immutable image digest returned by Cloud Build. Do not put a DB URL
or password in this JSON. Render first, then apply:

```bash
python3 src/deployment/cloud-run/deploy.py --config .local/cloud/run.json
python3 src/deployment/cloud-run/deploy.py --config .local/cloud/run.json --apply
```

The API listens on port 8000. Direct VPC egress reaches the private database and model. The
`healthops-run-model` firewall rule allows port 11434 from the Cloud Run network tag only.
The bootstrap script installs Docker on a driver-ready VM; `compose.model.yml` requires
`MODEL_PRIVATE_IP`. Run it only on the dedicated model VM, not against the old personal stack.

Database initialization used the existing Alembic migrations through an IAP SSH tunnel. The
`healthops_migrator` operator role owns the schema. The separate `healthops_api` runtime role has
CONNECT, schema USAGE and table SELECT/INSERT/UPDATE/DELETE, plus sequence privileges and matching
default privileges. It has no superuser, role-creation or database-creation privileges. Credentials
were generated randomly and kept in mode-0600 ignored local files; no service-account key was made.
Provisioning and initial role grants were operator actions, not an automated ML pipeline.

## Synthetic import and replay

The original local importer remains local-only. `seed_synthetic.py` is an explicit cloud operator
path: checksum/provenance validation -> account creation -> authenticated HTTP ingestion in batches
of at most 500. It reuses `prepare()` and `provision()` from `healthops.synthetic_import`.
It refuses a DB name other than `healthops_synthetic`, a different tunnel address/port, non-TLS
connections, untrusted API URLs, and databases containing identities outside the selected cohort.
Credentials and reports belong in ignored `.local/`, never Git.

Prerequisites: the API service Python environment with the `synthetic` extra; restored DVC dataset;
Google identity with Cloud Run invocation rights; a mode-0600 migration URL file targeting the
following tunnel. Keep this command running in a separate terminal:

```bash
gcloud compute ssh healthops-ms3-model --zone=us-east4-c \
  --project=your-gcp-project-id --tunnel-through-iap -- \
  -N -L 127.0.0.1:15433:10.51.0.3:5432 -o ExitOnForwardFailure=yes
```

The URL file uses PostgreSQL/psycopg, `127.0.0.1:15433`, database `healthops_synthetic` and
`?sslmode=require`. `PGGSSENCMODE=disable` avoids an observed local GSS negotiation delay while
retaining TLS. Run from the repo root; omit `--apply` for source validation without DB/API writes:

```bash
PGGSSENCMODE=disable PYTHONPATH=src/api-service \
  src/api-service/.venv/bin/python src/deployment/cloud-run/seed_synthetic.py \
  --dataset data/synthetic/synthetic-v0.1 \
  --manifest data/manifests/synthetic-v0.1.yaml \
  --database-url-file .local/cloud/ms3/migrator-url \
  --api-url https://YOUR_SERVICE_URL.run.app \
  --output-dir .local/cloud/ms3/synthetic --apply
```

Partial failures preserve completed batches. Fix the cause and rerun with the same source and
credentials directory: source-record uniqueness produces duplicates instead of extra observations.
Conflicting payloads fail instead of overwriting. Application tokens expire after seven days;
renew them with the operator CLI when needed. Reports are not a credential backup.

The licensed reference corpus was separately ingested with the existing knowledge ingestion script
and the cloud `nomic-embed-text` endpoint. This is reference material, not additional user accounts.

## Access, logs and lifecycle

Service URL: `https://YOUR_SERVICE_URL.run.app`.
Unauthenticated visitors receive 403. Authorized requests need two separate headers:

- `X-Serverless-Authorization: Bearer <Google identity token>` for Cloud Run IAM.
- `Authorization: Bearer <synthetic account token>` for HealthOps user identity.

Reviewer identities have not been granted access because their emails are not specified. Grant
`roles/run.invoker` on this service to the intended reviewer, and share a synthetic account credential
privately. The existing iOS client does not implement Cloud Run IAM login; changing its base URL
alone is insufficient. Production user login and token lifecycle remain separate implementation work.

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="healthops-ms3-api"' \
  --project=your-gcp-project-id --limit=20

gcloud compute instances stop healthops-ms3-model --zone=us-east4-c --project=your-gcp-project-id
gcloud compute instances start healthops-ms3-model --zone=us-east4-c --project=your-gcp-project-id
```

Stopping the VM does not delete the model disk. To stop Cloud SQL compute when the demo is inactive,
use `gcloud sql instances patch healthops-ms3-synthetic --activation-policy=NEVER
--project=your-gcp-project-id`; this makes API database operations unavailable and storage charges
remain. Resume with `--activation-policy=ALWAYS`. Neither shutdown command was run during verification.
Full deletion is a separate explicit action; the SQL instance has deletion protection.

For production, create a separate project/environment, database, secrets and runtime identity;
keep synthetic evaluation data separate. Add real user authentication, client integration, account
lifecycle, backup/restore checks, availability planning, monitoring and load testing. No real records
were migrated into this development environment. No fine-tuning, Vertex Pipeline or W&B experiment
was implemented by this deployment.

## Local verification

```bash
cd src/api-service
.venv/bin/python -m pytest -q ../../scripts/tests
```

Tests cover deployment flags, explicit import targets, two independent authentication headers,
account-separated batches and maximum batch size. They do not prove cloud networking or model
accuracy; those require the separately recorded live checks.

References: [Cloud SQL creation](https://docs.cloud.google.com/sql/docs/postgres/create-instance),
[Cloud Run Direct VPC egress](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc).

## Frozen 30+6 cloud evaluation

The LangGraph image was deployed and evaluated on 2026-10-30. See the
[36-question report](../../../docs/evidence/cloud-langgraph-evaluation.md) for all results,
comparison limits and failed examples. `evaluate_http.py` checks frozen artifact, prompt and tool
schema hashes, verifies the three `/v1/me` identities, sends the original questions through HTTP,
and reuses the original `grade()` function. Raw results remain in ignored local storage.

Relative questions need the original per-account `today`. Use a separate IAM-protected tagged
revision with no default traffic and `HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE=true` for evaluation.
Keep normal traffic on the date-override-disabled revision, and remove the tag and evaluation
revision afterwards. Never enable frozen dates on the normal app endpoint just to run the set.

```bash
PYTHONPATH=src/api-service:src/api-service/scripts \
  src/api-service/.venv/bin/python src/deployment/cloud-run/evaluate_http.py \
  --dataset src/api-service/.local/eval/cohort-baseline-v2 \
  --probes src/api-service/.local/eval/natural-probes-v1.jsonl \
  --credentials .local/cloud/ms3/synthetic \
  --output src/api-service/.local/eval/NEW_RUN_DIRECTORY \
  --url https://YOUR_EVALUATION_TAG_URL.run.app \
  --project your-gcp-project-id --region us-east4 --service healthops-ms3-api \
  --revision YOUR_EVALUATION_REVISION
```

The original local run disabled retrieval; cloud evaluation enables the reference corpus. Results
must not be presented as an isolated LangGraph improvement or an overall answer-accuracy score.

## Routed multi-turn cloud diagnostics

`evaluate_routed_http.py` runs the new 63-scenario set through HTTP, including saved conversation
IDs and versions. It reuses the independently authored gold and grader from `eval_routed.py`.
Prerequisites include migration 0005, a running model, the existing synthetic operator tunnel,
and a tagged evaluation revision with zero normal traffic and frozen-date overrides enabled.

```bash
PGGSSENCMODE=disable PYTHONPATH=src/api-service:src/api-service/scripts \
  src/api-service/.venv/bin/python src/deployment/cloud-run/evaluate_routed_http.py \
  --dataset src/api-service/.local/eval/cohort-baseline-v2 \
  --credentials .local/cloud/ms3/synthetic \
  --database-url-file .local/cloud/ms3/migrator-url \
  --output src/api-service/.local/eval/NEW_ROUTED_RUN \
  --url https://YOUR_EVALUATION_TAG_URL.run.app \
  --project your-gcp-project-id --region us-east4 --service healthops-ms3-api \
  --revision YOUR_EVALUATION_REVISION
```

Use `--ids` with comma-separated scenario IDs for the smoke test. The runner checks the tagged
URL's revision and traffic allocation, proves all three authenticated identities, and requires
exactly those identities in the synthetic database. It does not import observations or migrate.
IAM tokens refresh during long runs. A pre-execution 429 can be retried once; timeouts and 5xx
are never automatically replayed because a conversation version might already be consumed.
HTTP transport errors stop the run for infrastructure diagnosis. Application `model_error`
responses remain scored failures: that status can also mean an invalid structured router output,
so it must not automatically be classified as network failure. Partial artifacts remain private under `.local/eval/`.
HTTP traces include exposed tool calls, request scope and verification attempts; raw internal
router model proposals are not exposed by the API. Reference cases still score routing only.

The [2026-10-31 routed cloud diagnosis](../../../docs/evidence/cloud-routed-evaluation.md)
records 3/63 passed scenarios and no requests reaching execution. Migration 0005 was applied
to the isolated synthetic DB. The new API remains a zero-traffic candidate; normal traffic was
not promoted because the router contract failed real-model QA.
