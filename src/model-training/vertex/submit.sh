#!/usr/bin/env bash
# Build the training image with Cloud Build, push it to Artifact Registry and submit one
# Vertex AI custom job that runs healthops-train on a synthetic version stored in the bucket.
#
# One-time project setup (an owner/editor runs it once; see README "Serverless training"):
#   gcloud services enable aiplatform.googleapis.com artifactregistry.googleapis.com \
#       cloudbuild.googleapis.com secretmanager.googleapis.com
#   gcloud artifacts repositories create healthops --repository-format=docker --location=$REGION
#   printf '%s' "$WANDB_KEY" | gcloud secrets create wandb-api-key --data-file=-
#   gsutil -m rsync -r data/synthetic/synthetic-v0.2 gs://$BUCKET/data/synthetic/synthetic-v0.2
#   gsutil cp data/manifests/synthetic-v0.2.yaml gs://$BUCKET/data/manifests/
#
# Usage (from the repository root, with gcloud logged in to the project):
#   src/model-training/vertex/submit.sh                 # full synthetic-v0.2, all three models
#   USERS=200 SMOKE=true src/model-training/vertex/submit.sh   # cheap first run
#   MODELS=lightgbm MACHINE_TYPE=n1-standard-8 src/model-training/vertex/submit.sh
#   ACCELERATOR_TYPE=NVIDIA_L4 MACHINE_TYPE=g2-standard-4 MODELS=transformer src/model-training/vertex/submit.sh
# Every variable below can be overridden from the environment. Nothing here is billable
# until the final `gcloud ai custom-jobs create`; Cloud Build itself costs cents.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROJECT="${PROJECT:-ac215-project-509705}"
REGION="${REGION:-us-east4}"
BUCKET="${BUCKET:-ac215-project-509705-healthops-data}"
REPOSITORY="${REPOSITORY:-healthops}"
DATA_VERSION="${DATA_VERSION:-synthetic-v0.2}"
MODELS="${MODELS:-lightgbm,transformer,diffusion}"
USERS="${USERS:-0}"
SMOKE="${SMOKE:-false}"
MACHINE_TYPE="${MACHINE_TYPE:-n1-highcpu-16}"
# GPU: ACCELERATOR_TYPE=NVIDIA_L4 ACCELERATOR_COUNT=1 MACHINE_TYPE=g2-standard-4 selects the CUDA
# image (TORCH_VARIANT, default cu132) and adds the accelerator to the job spec.
ACCELERATOR_TYPE="${ACCELERATOR_TYPE:-}"
ACCELERATOR_COUNT="${ACCELERATOR_COUNT:-1}"
if [ -n "$ACCELERATOR_TYPE" ]; then TORCH_VARIANT="${TORCH_VARIANT:-cu132}"; else TORCH_VARIANT="${TORCH_VARIANT:-cpu}"; fi
# The job runs as this service account: it must read and write the bucket, and the submitter
# needs roles/iam.serviceAccountUser on it. The build identity needs Artifact Registry writer
# and log writer; leave it empty to use the project's Cloud Build default.
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-healthops-vertex-training@${PROJECT}.iam.gserviceaccount.com}"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT:-healthops-build@${PROJECT}.iam.gserviceaccount.com}"
WANDB_PROJECT="${WANDB_PROJECT:-healthops-m3}"
WANDB_ENTITY="${WANDB_ENTITY:-}"
WANDB_SECRET="${WANDB_SECRET:-wandb-api-key}"
GIT_COMMIT="$(git -C "$ROOT" describe --always --dirty 2>/dev/null || echo unknown)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
JOB_NAME="${JOB_NAME:-healthops-train-${DATA_VERSION}-${STAMP}}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT}/${REPOSITORY}/model-training:${GIT_COMMIT}-${TORCH_VARIANT}}"

echo "project=$PROJECT region=$REGION bucket=$BUCKET"
echo "job=$JOB_NAME image=$IMAGE"
echo "version=$DATA_VERSION models=$MODELS users=$USERS smoke=$SMOKE machine=$MACHINE_TYPE accelerator=${ACCELERATOR_TYPE:-none} torch=$TORCH_VARIANT"
echo "job account=$SERVICE_ACCOUNT build account=${BUILD_SERVICE_ACCOUNT:-default}"
gcloud config set project "$PROJECT" >/dev/null

# Refuse to start a billable job when the data is not where the container will look for it.
gsutil -q stat "gs://${BUCKET}/data/synthetic/${DATA_VERSION}/users.parquet" \
  || { echo "gs://${BUCKET}/data/synthetic/${DATA_VERSION}/ is missing; rsync the version first (see header)"; exit 1; }

# W&B: the key is read from Secret Manager at submit time and handed to the job as an
# environment variable (visible to project editors in the job spec, never written to Git).
# Without the secret the run is offline and its files land in the bucket for a later `wandb sync`.
if WANDB_API_KEY="$(gcloud secrets versions access latest --secret="$WANDB_SECRET" 2>/dev/null)"; then
  WANDB_DIR="/tmp/wandb"
  echo "W&B: online (secret $WANDB_SECRET)"
else
  WANDB_API_KEY=""
  WANDB_DIR="/gcs/${BUCKET}/training/wandb"
  echo "W&B: no secret $WANDB_SECRET; offline files go to $WANDB_DIR"
fi

# Build once per commit; a rebuild of an existing tag is skipped.
if ! gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
  echo "building $IMAGE with Cloud Build"
  BUILD_ARGS=(--config "$ROOT/src/model-training/vertex/cloudbuild.yaml" --substitutions "_IMAGE=$IMAGE,_TORCH_VARIANT=$TORCH_VARIANT" --quiet)
  if [ -n "$BUILD_SERVICE_ACCOUNT" ]; then
    BUILD_ARGS+=(--service-account "projects/${PROJECT}/serviceAccounts/${BUILD_SERVICE_ACCOUNT}")
  fi
  gcloud builds submit "$ROOT/src/model-training" "${BUILD_ARGS[@]}"
else
  echo "image exists, skipping build"
fi

SPEC="$(mktemp)"
if [ -n "$ACCELERATOR_TYPE" ]; then
  ACCELERATOR_BLOCK="      acceleratorType: ${ACCELERATOR_TYPE}
      acceleratorCount: ${ACCELERATOR_COUNT}"
else
  ACCELERATOR_BLOCK=""
fi
export BUCKET DATA_VERSION MODELS USERS SMOKE JOB_NAME IMAGE GIT_COMMIT MACHINE_TYPE SERVICE_ACCOUNT \
  ACCELERATOR_BLOCK WANDB_PROJECT WANDB_ENTITY WANDB_API_KEY WANDB_DIR
# Fill the template from the environment (python3 ships with gcloud; envsubst does not everywhere).
# Vertex rejects an env entry with an empty value, so entries whose variable is unset are dropped
# (WANDB_ENTITY by default, WANDB_API_KEY without the secret).
python3 - "$ROOT/src/model-training/vertex/job.template.yaml" > "$SPEC" <<'PY'
import os, re, string, sys
text = string.Template(open(sys.argv[1]).read()).safe_substitute(os.environ)
lines = text.splitlines()
out = []
i = 0
while i < len(lines):
    line = lines[i]
    nxt = lines[i + 1] if i + 1 < len(lines) else ""
    if re.match(r"\s*- name: ", line) and re.match(r'\s*value: *(""|\'\')? *$', nxt):
        i += 2
        continue
    out.append(line)
    i += 1
print("\n".join(out))
PY
gcloud ai custom-jobs create --region="$REGION" --display-name="$JOB_NAME" --config="$SPEC"
rm -f "$SPEC"

cat <<EOF

Submitted. Follow it with:
  gcloud ai custom-jobs list --region=$REGION --filter="displayName=$JOB_NAME" --format="value(name,state)"
  gcloud ai custom-jobs stream-logs --region=$REGION \$(gcloud ai custom-jobs list --region=$REGION --filter="displayName=$JOB_NAME" --format="value(name)")
Console: https://console.cloud.google.com/vertex-ai/training/custom-jobs?project=$PROJECT
Outputs: gs://$BUCKET/training/runs/$JOB_NAME/ (RESULTS.md, metrics.json, models/, predictions.parquet)
EOF
