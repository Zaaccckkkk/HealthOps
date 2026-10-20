# Cloud development deployment (single GPU VM)

Status: **created and exercised once on 2026-10-03** (project `ac215-project-509705`,
`us-east4-a`, VM `healthops-gpu`, static address, dedicated network with two firewall rules).
Observed in that session: Let's Encrypt certificate issued through TLS-ALPN on the first start;
`qwen3:8b` (same digest as the Mac) loaded on the L4 with 9.8 GB of 23 GB GPU memory and the
context pinned to 32768; cold first request 97 s, warm p50 3.7 s; the ten-question protocol
regression and five questions from a physical iPhone over the public HTTPS address completed
with synthetic data only. Numbers are in `ml/eval/RESULTS.md`. The VM is stopped between
sessions; the disk and the address are retained.

This is a one-tester development deployment with synthetic data. It is not multi-tenant, not
highly available and not a production security posture.

## Shape

| Component | Where | Reachable from |
|---|---|---|
| Caddy (HTTPS edge, ACME certificates) | container, publishes 443 | the internet (firewall tcp/443) |
| FastAPI (`healthops`) | container, no published port | Caddy only, by service name |
| PostgreSQL 17 with pgvector (`pgvector/pgvector:pg17`) | container, no published port, bind mount `/srv/healthops/postgres` | the API only |
| Ollama + `qwen3:8b` | container with one NVIDIA L4, no published port, bind mount `/srv/healthops/ollama` | the API only |
| SSH | VM port 22 | Identity-Aware Proxy range `35.235.240.0/20` only |

Files: `infra/cloud/compose.cloud.yml`, `infra/cloud/Caddyfile`, `infra/cloud/env.cloud.example`
(copy to `.env.cloud` on the VM), `infra/cloud/startup.sh`, `scripts/cloud/vm.sh` (gcloud
wrapper), `scripts/cloud/remote.sh` (runs on the VM).

## Networking

The VM lives in a dedicated VPC network with exactly two firewall rules (443 from anywhere,
22 from the IAP range), both targeting the `healthops` tag. The project's default network is
not used because its default rules allow SSH from any address. Inside the VM, Compose puts all
containers on one bridge network; only Caddy publishes a host port, so PostgreSQL and Ollama are
unreachable from outside the VM regardless of firewall settings. The VM runs with no service
account, so it holds no Google Cloud credentials.

## HTTPS

Caddy requests a publicly trusted certificate for `HEALTHOPS_PUBLIC_HOST` through ACME (Let's
Encrypt first, ZeroSSL as fallback) using the TLS-ALPN challenge on port 443; port 80 is never
opened. The hostname is a magic-DNS name for the reserved static address (`<ip>.sslip.io`)
unless a domain is chosen later. Certificates and the ACME account persist in
`/srv/healthops/caddy`, so restarts re-use them instead of re-issuing. A failed issuance is a
visible error in `docker compose logs caddy`; the response is to fix the DNS/firewall cause, not
to disable App Transport Security or certificate validation on the phone. Caddy also blocks
`/debug/*`, `/docs` and `/openapi.json` at the edge, caps request bodies at 1 MB and gives the
API 150 s to answer (the assistant deadline is 120 s).

## GPU

The image family `common-cu129-ubuntu-2204-nvidia-580` (Deep Learning VM) ships the NVIDIA
driver. Compose hands one GPU to the Ollama container through the device reservation
(`driver: nvidia, count: 1, capabilities: [gpu]`), which requires the NVIDIA Container Toolkit
on the host; `scripts/cloud/remote.sh preflight` checks `nvidia-smi` both on the host and inside
a CUDA container before anything else starts. Ollama is pinned to one loaded model, one
parallel request and an explicit context length (32768, the configuration verified on the Mac);
`remote.sh status` records GPU memory, container memory and the loaded model so the actual
numbers go into the results, not estimates.

## Authentication and limits

The phone sends a development bearer token issued on the VM for a synthetic user
(`remote.sh token`). Authentication runs before anything else; then a per-user sliding-minute
limiter (`HEALTHOPS_ASSISTANT_RATE_LIMIT_PER_MINUTE`, default 10) answers 429 with
`Retry-After`; then the orchestrator's concurrency gate (1 on the VM) and deadline apply. No
anonymous route reaches the model. The API runs with `--no-access-log`, Caddy's access log is
discarded, and the orchestrator logs tool names and durations only, never question text or
tool results.

## Data on the disk

Since 2026-10-03 the PostgreSQL volume holds, besides synthetic accounts, one account with the
owner's real HealthKit history (uploaded by the owner from their phone under explicit consent,
all seven metrics, 14 days). It is stored in clear text, protected only by the VM's network
rules, the IAP-only SSH path and the bearer token; there is no application-level encryption
and no per-record deletion endpoint. Nothing from that account may be copied into the
repository, fixtures, evaluation sets or documentation.

## Google Health connector

The connector needs a **Web application** OAuth client of the same Google Cloud project (the
project that already has Google Health API access), with the redirect URI
`https://<public host>/v1/connections/google-health/callback`, the consent screen in Testing
with the tester's Google Account as a test user, and the three read-only Google Health scopes.
The client id, client secret and a Fernet key go into `.env.cloud` only. Caddy proxies the
callback path like any other API path. Stored refresh tokens are encrypted at rest with the
key; rotating the key means reconnecting. Disconnecting revokes at Google and deletes the
credential; deleting synced data is a separate, explicit action. First real run (2026-10-03):
authorisation and identity check succeeded; Google Health also returns Apple Health copies
(`platform: HEALTH_KIT`), which the connector skips; `dailyRollUp` returned no points for this
account, so steps are synced as intraday intervals and continuous heart rate is not synced.

## Persistence and deletion policy

PostgreSQL data, Ollama model files and Caddy certificates are bind mounts under
`/srv/healthops` on the VM's persistent boot disk. Stopping the VM (manually, by the two-hour
limit or by the in-guest halt) keeps the disk and everything on it. `docker compose down` removes
containers only. The only commands that delete data are `vm.sh destroy --yes` (deletes the VM
and its boot disk) and `vm.sh release-ip --yes`; both refuse to run without the flag and both
are operator decisions, never automatic.

## Cost control

- `--max-run-duration=2h --instance-termination-action=STOP` is set at creation and shown by
  `vm.sh verify-autostop` after every start. The first start/stop cycle must confirm the
  duration counts from each start.
- `infra/cloud/startup.sh` schedules `shutdown -h +115` on every boot as a second, independent
  stop.
- Observed on the first boot: the GCE scheduling block showed `maxRunDuration 7200s` /
  `STOP`, and the guest logged "Shutdown scheduled for 04:34:46 UTC" 115 minutes after boot.
- While stopped, the boot disk (100 GB pd-balanced) and the static address keep accruing;
  `vm.sh status` lists both so the standing cost is visible at every handoff.
- Setup time (driver check, image build, model pull) counts against the two hours; the model
  pull is about 5.2 GB and persists on the disk, so it happens once.

## Lessons from the first session

- The Deep Learning VM image ships the NVIDIA driver and the NVIDIA Container Toolkit but not
  Docker; `remote.sh bootstrap` installs Docker Engine and the Compose plugin from Docker's
  repository and runs `nvidia-ctk runtime configure --runtime=docker`.
- macOS `tar` adds AppleDouble `._*` files unless `COPYFILE_DISABLE=1` is set; Alembic imported
  them as migrations and failed with "source code string cannot contain null bytes".
  `vm.sh sync` now builds the tarball from `git ls-files` with that flag and deletes `._*` on
  the VM.
- Bind mounts written by the API container must be owned by uid 10001 (`appuser`).
- An empty `email` directive makes Caddy refuse its configuration; the directive was removed.
- A 64 KB body limit rejected 500-record synthetic batches with 413; the limit is 1 MB.

## First-time setup (one session, counts against the two hours)

Prerequisites on the Mac: `gcloud` installed and logged in, a project with billing enabled,
`GPUS_ALL_REGIONS` >= 1 and `NVIDIA_L4_GPUS` >= 1 in the chosen region, and
`.local/cloud/vm.env` copied from `infra/cloud/vm.env.example`. For the Fitbit connector you
also need a Web application OAuth client of the same project (see "Google Health connector").

```bash
scripts/cloud/vm.sh check              # read-only: quotas, image, machine type
scripts/cloud/vm.sh network            # free: dedicated VPC + two firewall rules
scripts/cloud/vm.sh reserve-ip --yes   # billable from here on
scripts/cloud/vm.sh create --yes       # prints maxRunDuration=7200s / STOP; the guest also arms shutdown -h +115
scripts/cloud/vm.sh sync               # uploads exactly the Git-publishable files as a tarball (no .env, .local, .git, notes)
scripts/cloud/vm.sh ssh
```

On the VM, from `~/healthops`:

```bash
scripts/cloud/remote.sh bootstrap      # Docker Engine + Compose plugin, NVIDIA runtime, data directories
cp infra/cloud/env.cloud.example .env.cloud && nano .env.cloud   # public host, DB password, Google client, Fernet key
sudo scripts/cloud/remote.sh preflight # nvidia-smi on the host and inside a CUDA container
sudo scripts/cloud/remote.sh up        # builds the API image, runs migrations, starts Caddy, API, PostgreSQL, Ollama
sudo scripts/cloud/remote.sh pull-model  # qwen3:8b (5.2 GB) and nomic-embed-text (274 MB); both persist on the disk
sudo scripts/cloud/remote.sh knowledge   # fetches the reference sources and ingests them into pgvector (about 600 chunks)
sudo scripts/cloud/remote.sh token my-synthetic-tester   # 0600 credential file; type the token into the phone, then delete it
sudo scripts/cloud/remote.sh status    # containers, GPU memory, loaded model
```

If a publisher refuses the VM's address (observed 2026-10-18: the WHO repository answered
403 to the fetch from Google Cloud while the same download worked from the Mac), copy the
already-verified files instead of re-downloading: on the Mac,
`tar -czf raw.tgz -C .local/knowledge/raw .` (with `COPYFILE_DISABLE=1`), `gcloud compute scp`
the archive to the VM, extract it into `/srv/healthops/knowledge`, `chown -R 10001:10001` that
directory and re-run `remote.sh knowledge`. The fetch step skips files that already exist and
still verifies every sha256 against `knowledge/sources.yaml`, so the corpus on the VM is
byte-identical to the one checked on the Mac.

Then from the Mac: `curl https://<host>/health/ready` must return `{"status":"ready"}` with a
Let's Encrypt certificate. The first assistant question loads the model (about 100 s on an
L4) and may hit the 120 s deadline; the second answers in a few seconds. Stop with
`scripts/cloud/vm.sh stop`. The verified versions were: image
`common-cu129-ubuntu-2204-nvidia-580-v20261001` (driver 580.178.04), Ollama 0.35.1 (pinned
in Compose), Caddy 2.10, PostgreSQL 17, Docker Engine from Docker's stable repository at the
time of bootstrap (not pinned; `remote.sh bootstrap` prints the installed version).

## Later sessions

On the Mac, from the repository root:

```bash
scripts/cloud/vm.sh start      # prints the auto-stop block again
scripts/cloud/vm.sh sync       # only if code changed
scripts/cloud/vm.sh ssh        # lands in the home directory of the VM
```

On the VM (SSH opens in `~`, so change directory first):

```bash
cd ~/healthops
sudo scripts/cloud/remote.sh up        # rebuilds the API image only if the code changed; migrations run first
sudo scripts/cloud/remote.sh status
# ... use it from the phone ...
sudo scripts/cloud/remote.sh down      # containers only; data stays on the disk
exit
```

Back on the Mac:

```bash
scripts/cloud/vm.sh stop && scripts/cloud/vm.sh status   # TERMINATED, disk and address retained
```

Protocol regression on the VM: `PYTHONPATH=services/ingestion-api/src uv run python
scripts/eval_assistant.py --set protocol` against the synthetic evaluation user, with the raw
outputs copied back to `.local/eval/` and the counts added to `ml/eval/RESULTS.md`.
