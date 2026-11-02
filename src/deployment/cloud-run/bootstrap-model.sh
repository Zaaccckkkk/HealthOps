#!/bin/bash
# Run on a clean Deep Learning VM image with NVIDIA drivers and container toolkit.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
mkdir -p /srv/healthops/ollama
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
