#!/bin/bash
# Runs ON the VM from ~/healthops. Brings the stack up or down and reports the measurements
# the deployment notes ask for (GPU memory, container memory, model digest and context).
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose --env-file .env.cloud -f infra/cloud/compose.cloud.yml)
case "${1:-help}" in
  bootstrap)  # first boot only: the Deep Learning VM image ships the NVIDIA driver and the
              # NVIDIA Container Toolkit but not Docker. Installs Docker Engine + Compose plugin
              # from Docker's repository, registers the NVIDIA runtime and prepares data dirs.
    export DEBIAN_FRONTEND=noninteractive
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
    sudo nvidia-ctk runtime configure --runtime=docker >/dev/null
    sudo systemctl restart docker
    sudo usermod -aG docker "$USER"
    sudo mkdir -p /srv/healthops/postgres /srv/healthops/ollama /srv/healthops/caddy/data /srv/healthops/caddy/config /srv/healthops/credentials /srv/healthops/eval
    sudo chown 10001:10001 /srv/healthops/credentials /srv/healthops/eval   # API container user
    sudo chmod 700 /srv/healthops/postgres /srv/healthops/credentials
    docker --version; docker compose version; nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
    echo "bootstrap done; log out and back in (or use sudo) for the docker group to apply"
    ;;
  preflight)
    nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv
    docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu22.04 nvidia-smi -L
    df -h / /srv/healthops 2>/dev/null || df -h /
    ;;
  up)       "${COMPOSE[@]}" build; "${COMPOSE[@]}" up -d; "${COMPOSE[@]}" ps ;;  # migrate runs first
  pull-model)
    "${COMPOSE[@]}" exec ollama ollama pull "${OLLAMA_MODEL:-qwen3:8b}"
    "${COMPOSE[@]}" exec ollama ollama show "${OLLAMA_MODEL:-qwen3:8b}" | head -20
    ;;
  status)
    "${COMPOSE[@]}" ps
    nvidia-smi --query-gpu=memory.total,memory.used --format=csv
    docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'
    "${COMPOSE[@]}" exec ollama ollama ps
    ;;
  logs)     "${COMPOSE[@]}" logs --tail 100 "${2:-caddy}" ;;
  token)    # synthetic tester only. Writes a 0600 credential file under /srv/healthops/credentials
            # on the VM; type the token into the phone, then delete the file. Tokens expire in 7 days.
    # The API container runs as uid 10001 (appuser); the bind mount must be writable by it.
    sudo mkdir -p /srv/healthops/credentials && sudo chown 10001:10001 /srv/healthops/credentials && sudo chmod 700 /srv/healthops/credentials
    "${COMPOSE[@]}" run --rm -v /srv/healthops/credentials:/creds api \
      healthops create-user --name "${2:-synthetic-phone-tester}" --output "/creds/${2:-synthetic-phone-tester}.json"
    echo "credential file: /srv/healthops/credentials/${2:-synthetic-phone-tester}.json (delete after use)"
    ;;
  down)     "${COMPOSE[@]}" down ;;   # containers only; /srv/healthops data stays
  *) echo "usage: $0 {bootstrap|preflight|up|pull-model|status|logs [service]|token [name]|down}" ;;
esac
