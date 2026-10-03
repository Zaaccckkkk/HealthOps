#!/bin/bash
# gcloud wrapper for the single GPU development VM. Every command prints what it is about to
# run; billable actions (reserve-ip, create) refuse to run without --yes. Settings come from
# .local/cloud/vm.env (ignored by Git):
#   PROJECT=...  ZONE=us-east4-a  VM=healthops-gpu  MACHINE=g2-standard-4  DISK_GB=100
#   NETWORK=healthops-net  ADDRESS=healthops-ip  IMAGE_FAMILY=common-cu129-ubuntu-2204-nvidia-580
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${CLOUD_ENV_FILE:-$ROOT/.local/cloud/vm.env}"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (see docs/cloud-dev-deployment.md)"; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${PROJECT:?}" "${ZONE:?}" "${VM:=healthops-gpu}" "${MACHINE:=g2-standard-4}" "${DISK_GB:=100}"
: "${NETWORK:=healthops-net}" "${ADDRESS:=healthops-ip}" "${IMAGE_FAMILY:=common-cu129-ubuntu-2204-nvidia-580}"
: "${MAX_RUN:=2h}"
REGION="${ZONE%-*}"
G=(gcloud --project "$PROJECT")

run() { echo "+ $*"; "$@"; }
need_yes() { [ "${YES:-}" = "1" ] || { echo "billable or public-facing: re-run with --yes"; exit 2; }; }

cmd="${1:-help}"; shift || true
for arg in "$@"; do [ "$arg" = "--yes" ] && YES=1; done

case "$cmd" in
  check)  # read-only
    run "${G[@]}" config list
    run "${G[@]}" billing projects describe "$PROJECT" || true
    run "${G[@]}" compute project-info describe --format='value(quotas.filter(metric:GPUS_ALL_REGIONS))'
    run "${G[@]}" compute regions describe "$REGION" --format='value(quotas.filter(metric:NVIDIA_L4_GPUS))'
    run "${G[@]}" compute images describe-from-family "$IMAGE_FAMILY" --project deeplearning-platform-release --format='value(name,diskSizeGb,creationTimestamp)'
    run "${G[@]}" compute machine-types describe "$MACHINE" --zone "$ZONE" --format='value(guestCpus,memoryMb,accelerators)'
    ;;
  network)  # free: dedicated VPC with only our two rules (the default network allows SSH from anywhere)
    run "${G[@]}" compute networks create "$NETWORK" --subnet-mode=auto
    run "${G[@]}" compute firewall-rules create "$NETWORK-https" --network "$NETWORK" --allow tcp:443 --target-tags healthops --source-ranges 0.0.0.0/0
    run "${G[@]}" compute firewall-rules create "$NETWORK-iap-ssh" --network "$NETWORK" --allow tcp:22 --target-tags healthops --source-ranges 35.235.240.0/20
    ;;
  reserve-ip) need_yes
    run "${G[@]}" compute addresses create "$ADDRESS" --region "$REGION" --network-tier PREMIUM
    run "${G[@]}" compute addresses describe "$ADDRESS" --region "$REGION" --format='value(address)'
    ;;
  create) need_yes
    run "${G[@]}" compute instances create "$VM" --zone "$ZONE" --machine-type "$MACHINE" \
      --image-family "$IMAGE_FAMILY" --image-project deeplearning-platform-release \
      --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced --maintenance-policy TERMINATE \
      --max-run-duration "$MAX_RUN" --instance-termination-action STOP \
      --network "$NETWORK" --address "$ADDRESS" --tags healthops --no-service-account --no-scopes \
      --metadata-from-file startup-script="$ROOT/infra/cloud/startup.sh" \
      --metadata enable-oslogin=FALSE
    "$0" verify-autostop
    ;;
  verify-autostop)
    run "${G[@]}" compute instances describe "$VM" --zone "$ZONE" --format='yaml(scheduling.maxRunDuration,scheduling.instanceTerminationAction,status)'
    ;;
  start) run "${G[@]}" compute instances start "$VM" --zone "$ZONE"; "$0" verify-autostop ;;
  stop)  run "${G[@]}" compute instances stop "$VM" --zone "$ZONE" ;;
  status)
    run "${G[@]}" compute instances list --filter="name=$VM" --format='table(name,zone,status,machineType.basename(),networkInterfaces[0].accessConfigs[0].natIP)'
    run "${G[@]}" compute disks list --filter="zone:$ZONE" --format='table(name,sizeGb,type.basename(),users.basename())'
    run "${G[@]}" compute addresses list --format='table(name,address,status,region.basename())'
    ;;
  ssh) run "${G[@]}" compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap -- "$@" ;;
  sync)  # upload exactly the Git-publishable files (tracked + untracked, ignore rules applied):
         # never .env*, .local/, .git/ or local learning notes. COPYFILE_DISABLE stops macOS tar
         # from adding AppleDouble "._*" files, which Alembic would otherwise import as migrations.
    list="$(mktemp)"; tgz="$(mktemp -t healthops-src).tgz"
    (cd "$ROOT" && git ls-files --cached --others --exclude-standard > "$list")
    if grep -E '^\.env($|\.)|^\.local/|^docs/learning/|^\.git/' "$list" | grep -v '^\.env\.example$'; then
      echo "refusing to sync: local-only paths in the file list"; exit 3
    fi
    (cd "$ROOT" && COPYFILE_DISABLE=1 tar -czf "$tgz" -T "$list")
    echo "+ upload $(wc -l < "$list" | tr -d ' ') files"
    run "${G[@]}" compute scp "$tgz" "$VM:~/healthops-src.tgz" --zone "$ZONE" --tunnel-through-iap --quiet
    run "${G[@]}" compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --quiet --command \
      'mkdir -p ~/healthops && cd ~/healthops && find . -mindepth 1 -maxdepth 1 ! -name .env.cloud -exec rm -rf {} + && tar -xzf ~/healthops-src.tgz && rm ~/healthops-src.tgz && find . -name "._*" -delete && chmod +x scripts/cloud/*.sh && echo synced'
    rm -f "$list" "$tgz"
    ;;
  destroy) need_yes  # deletes the VM AND its boot disk (database and model); addresses and network are separate steps
    echo "This deletes the VM and its boot disk, including the PostgreSQL data and the model files."
    run "${G[@]}" compute instances delete "$VM" --zone "$ZONE" --delete-disks all
    ;;
  release-ip) need_yes; run "${G[@]}" compute addresses delete "$ADDRESS" --region "$REGION" ;;
  *)
    echo "usage: $0 {check|network|reserve-ip|create|verify-autostop|start|stop|status|ssh|sync|destroy|release-ip} [--yes]"
    ;;
esac
