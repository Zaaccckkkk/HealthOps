# Cross-device reconciliation tool

The API can serve the team's **14-day Transformer** as a CPU tool. Qwen3-8B remains the
language model: it chooses a tool and cites evidence; it does not replace the trained
reconciliation model or generate health measurements. Training results and the w14/w30
selection are recorded in [pipeline-window-comparison.md](evidence/pipeline-window-comparison.md).

## Request and evidence

Authenticated `GET /v1/tools/reconcile?metric=steps&date=2026-08-07&timezone=America/New_York`
accepts exactly one date and one of `sleep_duration`, `steps`, `resting_heart_rate`.
It uses the bearer-token user ID, never a user ID from the request. The assistant exposes
this as `reconcile_devices(metric, date)`; the server injects timezone. Select **All channels**
in the app. A filtered source selection cannot silently gain access to other channels.

```text
Question -> Qwen intake router -> missing metric/date? -> persistent clarification + resume
                             -> unsupported? -> fixed refusal
                             -> device_reconciliation
                                  -> Qwen calls reconcile_devices(metric, date)
                                  -> user-scoped daily summaries of all three input metrics
                                  -> CPU Transformer (one metadata-defined window)
                                  -> evidence card with estimate, interval, readings and warnings
                                  -> Qwen cites card via state_findings
                                  -> server validates date, claim and answer coverage
                                  -> qualified answer, or one bounded correction / fallback
```

The result includes `q05`, `q50`, `q95`, `providers[].value`, signed `providers[].deviation`,
`attribution`, window dates and counts, sorted warning codes/definitions, checkpoint SHA-256,
model run and training data version. `data_version` identifies the **training dataset**, not a
claim that an arbitrary user's input records came from that version. Sleep is returned in seconds,
steps in count and resting heart rate in bpm. The model internally uses sleep minutes and log1p
steps; the serving adapter applies the exact inverse transforms.

`attribution` identifies the channel farther from q50 by absolute deviation. It is null for a tie,
a single usable channel, or no readings. This is **not evidence that a device is truly less accurate**.
A channel can contain multiple physical devices; the API warns when distinct device labels occur.

## Required behavior

| Condition | Tool and answer behavior |
| --- | --- |
| No usable target reading on the query day | q05/q50/q95 and attribution are null; earlier history cannot create a reading. `no_reading_on_date` and `no_records_in_window` qualify the requested date. |
| Less history than the model window | `history_short`; final answer includes **Data may not be sufficient; the result may not be reliable.** |
| Fewer than half of window days have a usable target reading | `sparse_window`, with the same mandatory red flag. For w14 the threshold is fewer than seven days. |
| Only one query-day channel has data | `single_device` and the mandatory red flag; estimate is allowed, but attribution is null and the missing channel is explicit. |
| q95-q05 exceeds 90 min sleep, 5,000 steps, or 8 bpm | `wide_interval` and the mandatory red flag. Threshold comparison uses API units. |
| Missing metric or date | Clarify; single-day choices plus free text. Replies resume the persisted conversation. |
| Month reconciliation, HRV reconciliation, health judgments | Refuse. Ordinary month summaries of recorded data remain supported. |

`history_days` is the calendar span from the earliest accessible observation of the three supported
input metrics through the query day, inclusive, capped at the model window. It does not imply daily
coverage. `window_days_with_any` and `window_days_with_both` count usable **target-metric** readings
inside the window. Missing values carry masks; zero is a legitimate value, never a missing marker.

Every accepted reconciliation finding renders the complete qualified result. The model cannot omit
an interval or warning by selecting only `device_attribution`. A missing-reading answer contains
no health estimate; calendar dates and model metadata are not prohibited by the no-number rubric.
If verification still fails after one correction, the answer remains unverified and the cards
remain available; server-owned warning text is preserved in the fallback as well.

## Training/serving limitations

- This is a synthetic-trained model, not clinical validation or identification of real device accuracy.
  q05-q95 is a **nominal** 90% interval; training-set coverage does not prove real-world calibration.
- Training uses stress, workout, travel, illness and fatigue. These are not collected by the current
  app. Serving uses generator defaults (`low`, `none`, false, false, 3) and always emits
  `context_defaulted`; these defaults are not inferred user facts.
- Training groups sleep by a latent bedtime unavailable to the application. Existing API tools group
  records by local start day. Serving deliberately reuses those documented summaries and emits
  `sleep_alignment_differs` when sleep features are present. This can affect all three outputs.
  Matching the neural architecture does **not** eliminate this distribution shift. A future version
  should train on an observation-only night grouping shared with serving.
- Source timezones in training and requested timezone in serving may differ. Synthetic diagnostics
  use each account's original `America/New_York` zone. Travel/timezone shifts need separate validation.
- Multiple/unaligned daily records and overlapping intervals retain existing aggregation warnings.
  Mixed total kinds are not chosen silently for model features.
- There is no medical diagnosis, future forecast, month reconciliation or per-physical-device ranking.

## Reproduce locally

The artifact pin is [reconcile-model.json](../src/api-service/reconcile-model.json). Download the two
files from its `uri` into ignored `src/api-service/.local/reconcile/w14/`, and copy that manifest as
`serving.json` alongside them. Startup verifies both SHA-256 values, uses `torch.load(weights_only=True)`
on CPU and strict state-dict loading. No request downloads models or accepts a model path.

```bash
# From repository root, with access to the team's GCS bucket:
mkdir -p src/api-service/.local/reconcile/w14
gcloud storage cp \
  gs://ac215-project-509705-healthops-data/pipelines/runs/20261107T231608Z/w14/models/transformer/transformer.pt \
  gs://ac215-project-509705-healthops-data/pipelines/runs/20261107T231608Z/w14/models/transformer/meta.json \
  src/api-service/.local/reconcile/w14/
cp src/api-service/reconcile-model.json src/api-service/.local/reconcile/w14/serving.json
docker compose -f docker-compose.yml -f compose.reconcile.yml up --build api
```

Alternatively, inside `src/api-service`, `uv sync --extra reconcile` and set
`HEALTHOPS_RECONCILE_MODEL_DIR` to the downloaded artifact directory. An unset directory disables
model loading; the endpoint returns 503 rather than fabricated values. Invalid configured artifacts
fail startup. The checkpoint is loaded once per API process, not once per question.

After retrieving the checksum-verified v0.2 DVC data, import with
`SYNTHETIC_DATA_VERSION=synthetic-v0.2 make synthetic-import` **only for the local Compose database**.
Versioned imports create different synthetic account UUIDs; they do not overwrite v0.1 identities.
Use the new credentials. Never redirect this command at the personal cloud database.

## Evaluation

[assistant_reconcile.synthetic.jsonl](../src/api-service/eval/assistant_reconcile.synthetic.jsonl)
contains **42 scenarios / 45 turns**: 14 families for Apple-only, Fitbit-only and paired users.
It includes quoted input numbers, single-channel questions, no query-day readings, three-day
history, sparse windows, missing scope with resume, month refusal, medical refusal and unsupported HRV.
A question mentioning two wearables does not manufacture two channels for a single-channel user.
Warning expectations differ by cohort and are frozen from the real v0.2 tool output.

The additional rubric columns are `red_flag_when_insufficient`, `no_number_when_no_reading`,
`interval_stated` and `attribution_matches_card`, each yes/no/n_a. Existing scope, coverage,
evidence and verification checks remain mandatory. Altering a response to remove a caveat or
invent a number must fail the evaluator's own tests.

Run inside `src/api-service`, with an isolated `healthops_test` database and a completed v0.2
import report (or the explicitly validated synthetic Cloud SQL tunnel and `--api-url`):

```bash
uv run --extra reconcile --extra synthetic python scripts/eval_reconcile.py \
  --database-url-file .local/eval/reconcile-v02/test-url \
  --import-report .local/synthetic-import-v02-test/report.json \
  --model-dir .local/reconcile/w14 \
  --output .local/eval/reconcile-v02/local-qwen \
  --model-url http://127.0.0.1:11434
```

`--freeze-only` computes gold without calling Qwen. Frozen results and raw responses stay ignored;
passing protocol tests does not measure the Transformer's physiological accuracy. The three-account
set is a development diagnostic, not a statistically independent model generalization benchmark.

## Cloud handoff

Build with [cloudbuild-reconcile.yaml](../src/deployment/cloud-run/cloudbuild-reconcile.yaml) using
source `src/api-service`. It fetches the pinned artifact, checks hashes and installs CPU torch.
The build identity needs read access to the artifact objects; the runtime needs no GCS credentials
for the bundled weights. Set `reconcile_model_dir` to `/app/models/reconcile` in the ignored deployment
config. Use a tagged evaluation revision first; do not send test traffic to the personal database.

[seed_synthetic.py](../src/deployment/cloud-run/seed_synthetic.py) continues to reject personal or
unrecognized database accounts. For v0.2 import, `--retain-import-report` explicitly permits the
previous completed v0.1 import after checking its manifest, derived UUIDs and account names.
It retains the old accounts; it never deletes their data. The resulting synthetic database can
therefore contain six accounts (three per version), while the new evaluation uses only v0.2.
The [local execution report](evidence/reconciliation-local-2026-11-08.md) records the initial
local-only handoff. The subsequent [cloud rollout](evidence/reconciliation-cloud-2026-11-08.md)
records the v0.2 import, immutable image, deployed revisions and observed cloud diagnostics.
