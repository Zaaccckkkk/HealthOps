#!/bin/bash
# Compute Engine startup script (runs on every boot as root). Second guard behind the VM's
# max-run-duration: the guest halts itself after 115 minutes, which stops the VM without
# deleting anything. Also prepares the persistent data directories.
set -euo pipefail
shutdown -h +115 "HealthOps cloud-dev guard: automatic halt after 115 minutes" || true
mkdir -p /srv/healthops/postgres /srv/healthops/ollama /srv/healthops/caddy/data /srv/healthops/caddy/config
chmod 700 /srv/healthops/postgres
logger "healthops startup: halt scheduled in 115 minutes; data directories ready"
