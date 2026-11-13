# Monitoring: dashboard and alert for the deployed API

Cloud Logging already holds every request, job and pipeline step (see the root README,
"Monitoring"). This folder adds the two things logs alone do not give: a Cloud Monitoring
dashboard that shows the service's health at a glance, and an alert that tells someone when
the API returns server errors. Both are plain JSON applied with one `gcloud` command each, so
they are reproducible and reviewable like the rest of the deployment.

| File | What it creates |
|---|---|
| `dashboard.json` | Dashboard **HealthOps API (Cloud Run)**: request rate by response class (2xx / 4xx / 5xx), p50 and p95 latency, 5xx per minute, container instances, CPU and memory utilization |
| `alert-5xx.json` | Alerting policy that fires when the service returns any 5xx response within five minutes; the documentation text in the policy tells the on-call person where to look |
| `personal-dashboard.json` | Equivalent dashboard scoped to `healthops-personal-api` |
| `personal-alert-5xx.json` | Equivalent 5xx policy scoped to the personal service |

All metrics are the ones Cloud Run publishes on its own (`run.googleapis.com/*`); nothing had to be
instrumented in the application, and the dashboard reads them for the service `healthops-ms3-api`
in `us-east4`. Training jobs and pipeline runs have their own pages in Vertex AI and their curves
in W&B, so they are not duplicated here.

Both dashboards and both enabled policies were verified in the cloud on November 12, 2026.
Neither policy has a notification channel configured: incidents are visible in Cloud Monitoring,
but no email or other external notification is sent.

## Create or update

From the repository root, as a project owner or `roles/monitoring.editor`:

```bash
gcloud monitoring dashboards create --config-from-file=src/deployment/monitoring/dashboard.json --project=ac215-project-509705
gcloud alpha monitoring policies create --policy-from-file=src/deployment/monitoring/alert-5xx.json --project=ac215-project-509705
```

For the personal service, use `personal-dashboard.json` and `personal-alert-5xx.json` with the
same commands. List existing resources first; do not create duplicates on every API deployment.

The first command prints the dashboard's resource name; open it at
`https://console.cloud.google.com/monitoring/dashboards?project=ac215-project-509705`. To change a
chart, edit the JSON and recreate the dashboard (an in-place `update` needs the live etag, which
the file does not carry, and a dashboard holds no history worth keeping):

```bash
NAME=$(gcloud monitoring dashboards list --project=ac215-project-509705 --filter="displayName='HealthOps API (Cloud Run)'" --format="value(name)")
gcloud monitoring dashboards delete "$NAME" --project=ac215-project-509705 --quiet
gcloud monitoring dashboards create --config-from-file=src/deployment/monitoring/dashboard.json --project=ac215-project-509705
```

To receive the alert somewhere, attach a notification channel (email) to the policy in the
console under Alerting → the policy → Edit → Notifications; channels hold personal addresses and
are therefore not part of the repository.

## Reading the dashboard

- **Request count by response class**: 2xx are served requests, 4xx are rejected ones (401 and
  403 are expected on this IAM-protected service when a caller has no token), 5xx are the ones to
  act on.
- **Latency p50 / p95**: tool calls answer in tens of milliseconds; `/v1/assistant/ask` takes
  seconds because it waits for the model VM, so p95 rises whenever assistant traffic is present.
- **Instances**: the service scales to zero when idle; the first request after an idle period
  shows as a latency spike (cold start), not an error.
- **5xx per minute** should stay at zero; Monitoring shows "No data" rather than a zero line when
  there were none, which is the healthy state. A 503 from the assistant route means the model VM
  is stopped (it halts itself after two hours); start it with the command in the Cloud Run README.

Observed state and screenshots: [docs/screenshots](../../../docs/screenshots/README.md)
(`cloud-monitoring-dashboard.png`, `cloud-monitoring-alert.png`).
