# Cloud phone readiness — November 12, 2026

## Deployment scope

The initial audit below recorded both Cloud Run APIs serving the API source from commit
`65e9062e9d99c5fe9d376b8c5597a46f41487c45`:

| Environment | Serving revision | Application access |
|---|---|---|
| Synthetic | `healthops-ms3-api-guardrails-1112` | Cloud Run IAM and an application token |
| Personal | `healthops-personal-api-guardrails-1112` | Application token over public HTTPS |

Cloud Build `2d4a33ec-a09f-4d45-9142-9d235ef730df` produced image digest
`sha256:5af91b2957d2e8c468b5b2cb7814bd972c131d4fe1ea96260039ec17ec99e727`.
Both services receive 100% of default traffic on their new revision; all seven existing/new
traffic tags point to these revisions. Separate database secrets, runtime identities, data modes,
OAuth settings and resource limits were preserved. Writer is enabled on both services.

The image includes input/final guardrails, unsupported-script handling, v6 computed evidence,
output formatting, Writer, one bounded Editor attempt and verified-template fallback. There is
no separate local Python service required for these stages. Reconciliation uses the existing
image-bundled Transformer artifact; the LLM remains `qwen3:8b`, not an unfinished fine-tuned model.

## Runtime inventory

| Component | Observed state |
|---|---|
| API, tools, conversation storage, formatting and guards | Current shared API image on both Cloud Run services |
| PostgreSQL and pgvector | Both databases reachable; schema revision `0005` matches the current migration head |
| Reference library | Each database has 12 sources and 570 chunks; all three project-owned sources match current local text |
| Embeddings and chat | `nomic-embed-text` and `qwen3:8b` present on the shared private V100/Ollama VM |
| Google Health connector | Personal configured and connected; synthetic intentionally unconfigured for real-provider authorization |
| Monitoring | Existing synthetic dashboard/policy retained; equivalent personal dashboard and enabled 5xx policy created and read back |
| Native frontend | Installed/built separately with Xcode; it is not a cloud service |
| Data generation and training | Offline/batch workflows, not phone request services; not duplicated into the personal environment or rerun during this audit |
| Browser interaction prototype | Network-free design preview, not the production frontend; not deployed as an additional application |

No new health observations were uploaded, copied or migrated during these checks. Personal
health tables were not queried. Database checks read schema/public-reference metadata only;
phone checks read authentication and connector status. Google sync and HealthKit upload remain
explicit phone actions.

## Verification

- Both APIs passed live/ready, authenticated identity/data-mode and missing-application-token
  checks. Input override probes returned `refused`, with no tool calls. The earlier candidate
  rollout also rejected normal and adversarial Korean-script probes on both environments.
- The personal phone URL was checked with only the existing application bearer token, without
  a Google IAM header. The authenticated identity was personal and the connector reported
  `configured=true`, `connected=true`. No OAuth credentials were returned or published.
- Real cloud embeddings produced a 768-dimensional query vector. Read-only retrieval against
  each database returned the expected project HRV reference above the configured threshold.
- Synthetic HTTP daily-summary and reconciliation requests returned 200; reconciliation
  produced an estimate from the pinned model. This is execution evidence, not clinical validation.
- A synthetic reference question about SDNN/RMSSD returned a reference card in 11.15 seconds
  through `lookup_reference` and `state_findings`. Reference cards deliberately use their
  reference template rather than the optional Writer.
- An ordinary synthetic summary question using ISO dates was incorrectly routed to
  `availability`. The model repeated a forbidden `metric` argument and the API returned
  `tool_error`. This failure was retained; deployment readiness does not imply routing accuracy.
- The previously exercised English-month question requested date clarification (1.95 seconds).
  Its ISO-date continuation completed `daily_summary` and `state_findings`, produced one card
  and a v6 Writer answer (`narrative_status=generated`) in 5.83 seconds. These are individual
  smoke timings, not a latency benchmark.
- Deployment/policy tests: 44 passed. The added regression verifies that enabling synthetic
  Writer changes only the environment flags, preserving IAM, secret mappings and resource
  flags. Personal deployment retains one Writer flag and its OAuth configuration. Ruff lint
  and formatting checks passed. No API image rebuild was needed for these operator-script and
  monitoring-file changes.

The deployment renderer now accepts `narrative_enabled` in the shared configuration path,
so the synthetic service can preserve Writer on subsequent deployments. Both private configs
pin the current immutable image and enable Writer. No secrets are in the public configs.

## Availability and limits

The model VM was started for the owner's upcoming phone test and left running. It retains a
7,200-second automatic STOP; for this start, the scheduled limit is approximately **18:15 on
November 12, 2026, America/New_York**. API/database health does not prove model availability
after that stop. Initial model warm-up took 121.55 seconds; the loaded Qwen model then reported
full GPU residency and a 32,768-token context. Idle model eviction and later restarts can require
another cold load.

Both enabled 5xx policies currently have no notification channels. Incidents can appear in
Cloud Monitoring, but no email/push notification delivery was configured or tested.

Phone acceptance still requires the owner's actual app interactions. The physical device must
use the personal endpoint; synthetic remains IAM-protected and rejects personal upload mode.
The model's demonstrated routing failure, unknown injection resistance and connector metric
coverage limitations remain. Full Docker integration, new training and a broad real-model
quality evaluation were not run in this deployment audit.

Public-content policy, changed Markdown links and an independent whitespace check passed. The
staged diff was empty. `git diff --check` could not complete because the local Git read returned
`mmap failed: Operation timed out`; this is recorded separately from the successful checks.
Deployment tooling, monitoring definitions and documentation changes remain uncommitted.
The temporary synthetic test token was revoked and the operator tunnel was closed; the owner's
existing phone credential and the running model VM were retained.


## Subsequent physical-device session

The owner confirmed that Overview displayed normally on the connected iPhone 15. The current
Swift sources then built successfully for a signed physical-device destination and were installed
and launched with `devicectl`, preserving the bundle identity. The first Xcode attempt blocked
in `NSFileCoordinator`; building a complete temporary source copy outside Desktop succeeded.
No application source changes were needed. Existing personal API readiness and token identity
checks passed without a Google IAM header. No health records were queried or uploaded by the
operator during this session.

After the earlier automatic stop, the shared inference VM restarted at **18:25:45 New York
time**. Its two-hour STOP limit remains enabled, giving an approximate **20:25:45 on November
12, 2026** test-window end. This supersedes the earlier session's 18:15 stop time.

The restarted host ran kernel `6.8.0-1073-gcp`, but the installed NVIDIA module only covered
`6.8.0-1070-gcp`. `modprobe` reported a missing module; the existing Ollama container could not
start because the NVIDIA runtime reported `nvml error: driver not loaded`. The operator checked
the package candidates and simulated the install before installing the matching proprietary
`linux-modules-nvidia-580-server-6.8.0-1073-gcp` package and the
`linux-modules-nvidia-580-server-gcp` tracking metapackage. No existing packages were removed
or upgraded in that repair.

After loading the module, `nvidia-smi` reported Tesla V100-SXM2-16GB, driver 580.178.04 and
16,384 MiB. The existing Ollama container started successfully. The tracking package reduces
the risk of missing modules on future kernel updates; it does not replace post-restart inference
checks. Homepage acceptance alone does not prove model availability or answer quality.

A subsequent non-health `Reply with OK.` request completed against Qwen3-8B after 91.19
seconds of cold loading. Ollama reported a 32,768-token context and full GPU residency. This
is a single model-serving smoke check, not a phone-to-Writer quality evaluation. The owner
can now continue Ask acceptance; no completed personal Assistant response is claimed here.
Public-content policy passed, and the staged diff remained empty. Application unit/integration
suites were not rerun because no application source changed in this session.


## Subsequent source-label and sensitive-output rollout

The owner selected a narrower final gate: sensitive credential requests/leaks and protected
prompt disclosure remain blocking; evidence, date/number and markup quality findings are
advisory feedback to one Editor and do not veto its final answer. Invalid response contracts,
unexpected tool calls, missing candidates and exhausted budgets can still preserve the template.
Generated prose remains `model_generated`, with no claim of factual or clinical verification.

Source keys and evidence IDs remain stable. Card titles and template sentences display Apple
Health / Google Health. The Google ordinary-heart-rate import limitation is now a request-wide
answer qualification, not a warning on an Apple Health card. Missing and overlapping records
retain their original semantics. No database migration or new phone binary is required.

Cloud Build `59921dda-deff-49b8-8a01-b06254d369f4` produced immutable image
`sha256:22ffd2be5a2d8e9ad7733482bb64c8e101be8a20b05314d98cbd5430b51f8ede`.
The source bundle contained only tracked public API files, with the working source fixes
included; it was not represented as a new Git commit. Its SHA-256 source-manifest hash was
`4c8f84eec79b09a3df8275ada615678612ae08f8d8794d0f1242777c8eb3a035`.
Application code was unchanged after that build; an additional protected-prompt regression
and test-line formatting were added afterwards.

Both candidates were checked without default traffic, then promoted:

| Environment | Current revision | Default traffic | Tags |
|---|---|---|---|
| Synthetic | `healthops-ms3-api-sensitive-1112` | 100% | All four tags point to this revision |
| Personal | `healthops-personal-api-sensitive-1112` | 100% | All five tags point to this revision |

The runtime spec matched each pre-rollout snapshot except for the image. Both candidate and
live services passed readiness, authenticated identity/data-mode, missing-token rejection and
a credential-extraction probe that executed no tools. Personal checks used only the application
token and did not query health history. A synthetic heart-rate conversation completed with
`routing_status=ready`, `narrative_status=generated`, no narrative rejection, and one card.
It took 7.51 seconds across its request/clarification sequence; this is one smoke observation,
not a latency benchmark. Its card title was readable, canonical identity was preserved, and
the Google connector qualification appeared once in the answer and nowhere in card warnings.

The combined API regression run passed 499 tests and skipped two PostgreSQL-dependent tests.
The security suite subsequently passed 122 tests including one new protected-prompt-copy
regression. Repository policy tests passed 44 tests with the API package on PYTHONPATH.
Ruff, public-content policy, Markdown links and `git diff --check` passed; the staged diff was
empty. Full Docker integration and unrelated data/training suites were not run in this rollout.
The local MS3 presenter notes were updated in place; slide visuals were preserved.

The temporary synthetic test token was revoked and its operator tunnel closed. The owner's
phone credential and the model VM were retained for continued phone testing.
