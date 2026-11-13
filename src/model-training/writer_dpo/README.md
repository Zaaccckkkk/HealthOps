# Qwen3-8B writer preference-training preparation

This component prepares a **final-writer-only DPO experiment** using NeMo RL with the
Megatron Core backend, BF16 frozen base weights and LoRA adapters. It is **not NF4 QLoRA**.
The application router, tool selection, SQL tools and evidence validation are unchanged.
The reconciliation models and `make train-vertex` remain separate workloads.

Status as of 2026-11-10: the pinned four-A100 runtime completed the three-step smoke,
passed the separate two-process checkpoint restore check (nine checks), and completed
60 formal optimizer steps on the approved 28/7 training/validation split. Final artifacts
were uploaded successfully. Validation preference accuracy is not generated-answer quality.
Adapter export and matched BF16 writer replay are the next acceptance gate; the phone API
continues to use its existing model until independent evaluation supports a deployment.

## Fixed experiment

The versioned [config.json](config.json) pins `Qwen/Qwen3-8B`, its HF revision, the NeMo RL
commit and hashes of the inspected upstream training/data interfaces. NeMo's own `uv.lock`
and Megatron Bridge submodule pin the GPU environment; do not install this into the CPU
reconciliation environment or mix arbitrary package versions.

| Setting | Value |
| --- | --- |
| Hardware | One node, four BF16-capable GPUs; planned A100 40 GB |
| Parallelism | TP=2, DP=2, PP=1, CP=1 |
| Adapter | LoRA rank 16, alpha 32, no dropout |
| Precision | BF16; no four-bit loading and no FP8 |
| Preference batch | 4 pairs globally, microbatch 1; each pair has two completions |
| Learning rate / DPO beta | 5e-6 / 0.1; pilot choices, not tuned results |
| Sequence bound | 8192 tokens; reject masked overlength examples |
| Smoke | At most 3 optimizer steps, same model/data/topology as training |
| Training | At most 10 epochs and 60 steps, whichever is reached first |
| Validation | All validation rows at start, each epoch and end |
| Checkpoints | Top two by validation loss; never select on the holdout |
| Execution bound | 7200 seconds for the training subprocess |

DP is derived by Megatron from world size / TP / PP / CP; it is not an invented NeMo
configuration field. The initial experiment does not compare topologies. A smoke run validates
execution and checkpoint creation, not answer quality or scaling performance. Check checkpoint
restore and export on the GPU before accepting the pilot. Ten epochs is a ceiling, not a target.

## Data approval and export

The final owner-approved pool contains 28 training and 7 validation pairs, with five additional
blocked cases excluded. On 2026-11-10 the owner confirmed the consolidated workbook, including
the latest three revised chosen completions. Historical pending decisions remain labeled as
superseded rather than being mistaken for the current approval. The bundle was re-exported
from that workbook with new pair approvals and a manifest bound to its SHA-256. The export
preserves the approved chosen wording and exact captured model inputs.
Previously reviewed development validation is not an independent benchmark.

`data.inspect` checks the exact captured messages and rejected model completion, synthetic
provenance, trace hashes, output shape and evidence references. It rejects changed writer
instructions, held-out traces, blocked candidates and train/validation group, prompt or evidence
overlap. It does not certify prose semantics: the reviewer must judge the chosen completion
against the actual evidence. Historical fixture replays remain labeled as reconstructions;
they are not relabeled as full-chain cohort runs.

Run from `src/model-training` with the service environment (or Python with PyYAML for preparation).
All paths below are examples of local artifact locations, not committed datasets:

```sh
python -m writer_dpo.data \
  --candidates ../../.local/outputs/writer-split-plan-20261108 \
  --system-prompt .local/writer-dpo-preparation-20261108/system-prompt.txt \
  --trace-root ../api-service/.local/eval/writer-multimetric-1108/full \
  --output .local/writer-review-next
```

The system prompt must be the unmodified `SYSTEM` from the application's
[narrative.py](../../api-service/healthops/assistant/narrative.py), not an Excel evidence summary.
The initial local preparation already saved that exact string. Audit output contains IDs,
hashes and a draft approval file. A reviewer approves the **specific prompt/chosen/rejected
hash and split**, identifies themselves and resolves any pending replacement. Do not approve
all rows automatically. Approval does not convert assistant-written prose into clinical truth.

To export after review, repeat the command with `--approvals <reviewed-file.json>` and a **new**
`--output .local/writer-bundle`. Export strips review commentary, spreadsheet summaries and
extra metadata. Only these three NeMo fields enter the loss:

- `prompt`: exact captured system/user message list, including the evidence packet;
- `chosen`: reviewed assistant JSON string (`answer` plus `evidence_ids`);
- `rejected`: exact observed assistant completion string.

The bundle manifest binds content hashes, split membership and approvals. Changing a completion
after review invalidates the approval; changing the export invalidates launch. Dataset contents
remain ignored and must be versioned privately with DVC before transfer; never commit them.

## Data contract v2 and presentation experiments

The v1 exporter above accepts only verbatim captures of the one pinned writer prompt. The next
preference round uses the writer prompt confirmed on 2026-11-10 (presentation rules for dates,
hours/minutes, everyday wording and no synonym repetition), reviewer-edited completions and
authored pairs. [contract_v2.py](contract_v2.py) adds a second, separately validated path and
leaves the v1 functions and the archived 2026-11-10 bundle untouched.

Prompt versions are registered in [config.json](config.json) under `writer_prompts`, each with
the exporter fingerprint and the raw SHA-256 of the text; `writer_system_current` names the only
version that may be exported or trained. The service's
[narrative.py](../../api-service/healthops/assistant/narrative.py) keeps every registered
version in `SYSTEM_VERSIONS`, selects `SYSTEM` through `PROMPT_VERSION` (checked against
`writer_system_serving` by the registry test), and exposes
`is_writer_system` so evaluation replays still recognize writer calls captured under an older
version. `writer_system_sha256` remains the v1 pin of the archived bundle. Status is recorded per
version in the registry: v4 remains the approved training-data prompt. v5 is registered as an
inference candidate and selected by `writer_system_serving`; it consumes Python-computed
duration display fields and source display names. Selecting v5 for inference does not change
the archived training bundle, approve v5 preference pairs, or deploy a running service.

Every v2 candidate row (`schema_version: healthops-writer-dpo-candidate-v2`) names its
`provenance`:

| Provenance | Byte-identical to a raw trace | Edited | Origin fields |
| --- | --- | --- | --- |
| `captured_verbatim` | system + user message, rejected | chosen only | v1 `verify_capture` unchanged |
| `captured_old_prompt_reused` | user message, evidence, rejected | system message replaced by the current version | `capture.trace_path/trace_sha256`, `captured_prompt_version` must be a registered older version |
| `reviewed_edited` | user message, evidence | chosen and/or rejected | as above plus `captured_rejected_text` (the observed output is kept for audit), `edit_basis`, `review` |
| `archive_derived` | none located | any | `archive.path/sha256/record`; never called a trace |
| `synthetic_independent` | none | authored | `synthetic_additions` lists every builder-added fact; `review` points at the reviewed row |

`text_origin` is free text ("draft H/I columns"); paths live only in `capture.trace_path` or
`archive.path`, under an ignored `.local` directory. Old traces and the v3 prompt are preserved;
no capture under the new prompt is fabricated.

`contract_v2.inspect` checks, for every row: the current registered prompt and
`prompt_version`; the user message shape; `evidence_packet` equal to the model input and
reproducible by the production builder rules (`daily_table` summaries, reconciliation
distances and ties recomputed); completion shape and evidence ids on both sides; language;
provenance per class; and isolation. [holdout_ids.json](holdout_ids.json) is a hash-only list
of 82 excluded cases: the 13 frozen regression cases, 7 archived validation rows, 5 blocked
rows, 25 added validation rows and 32 added test cases. Test roles remain in the source labels;
the isolation loader uses `regression_test` for both test groups. A
candidate may not match an entry's id, question hash, exact evidence fingerprint or, for
regression and validation entries, its metric/provider/window shape. Train and validation
rows may not share a group, evidence, prompt, user message or window shape, and chosen answers
that are at least 90 percent similar across splits are rejected. Append new validation or test
cases with `contract_v2.isolation_entry`. Answer warnings (numbers, dates or durations not
derivable from the packet, ISO dates, raw seconds, internal labels) are reported per row and
must be acknowledged verbatim in the approval. The 13 regression cases informed prompt and pair
design; they remain a regression set, not an independent generalization test.

`pair_sha256` now covers prompt, both completions, evidence, provenance, prompt version,
synthetic additions and the origin hashes, so any change invalidates an earlier approval. The
manifest schema `healthops-writer-dpo-v2` records the registry entry, provenance counts, the
isolation list hash and `derived_from_bundle`; `launch.verify_bundle` accepts it only for the
registered current prompt and still verifies the archived v1 bundle.

The original 7 validation rows were derived from the same traces as the archived bundle: user message,
evidence, chosen and rejected stay byte-identical and only the system message is the current
version (`captured_old_prompt_reused`). They still serve the preference validation loss only.
Generated answers are reviewed with
[writer_generation_review_template.md](../../api-service/scripts/writer_generation_review_template.md);
preference loss and generation review are reported separately.

```sh
python -m writer_dpo.contract_v2 \
  --candidates <.local candidates dir with train/validation.candidates.jsonl> \
  --system-prompt <.local file holding the registered current prompt> \
  --output .local/<new audit dir> [--derived-from <sha256 of archived validation.jsonl>]
```

Without `--approvals` this writes `audit.json` and `approvals.draft.json` with every decision
`pending`; it is not a training export. The initial 2026-11-11 audit covered 62 training
positions (23 retained or revised originals, 5 replacements and 34 additions) and 7 derived
validation rows. Subsequent reviewed exports use 62 training pairs and 32 validation pairs;
the 13 original and 32 added generation tests remain excluded from training. Approval belongs
to each private, hashed export, not to this README or a prompt registry status.

### Fixed-horizon learning-rate comparisons

`launch --config-file` accepts an experiment-specific configuration without changing archived
defaults. The reviewed comparison uses fresh base-model starts at learning rates `5e-6`,
`2e-6` and `1e-6`, with the same v5 prompt, Python presentation fields, 62/32 split, seed and
global batch size of 2. The private experiment configurations select v5 as their current
training prompt; the checked-in default retains v4 for reproducibility.

With `experiment_epoch_cap=12`, `max_epochs=12` and `max_steps=360`, the scheduler horizon
stays fixed while `stop_after_step=124` pauses each trial. Validation and checkpoint saves
occur every 31 optimizer steps; generated answers at steps 62 and 124 are reviewed separately
from preference loss. SFT weight remains zero. Ten warmup steps and a constant learning rate
after warmup keep the schedule identical before and after a potential continuation.

`save_optimizer=true` and `keep_top_k=16` retain the state required for the planned continuation.
[`checkpoint_state.py`](checkpoint_state.py) checks saved Adam moments, RNG state, scheduler
cursor, dataloader presence, step and consumed-pair count. It reads only trusted locally
produced PyTorch checkpoints. These checks establish saved-state presence, not a successful
functional or bitwise resume. Only a trial selected through generation review is eligible
to continue from its full step-124 checkpoint, up to 360 steps. Dataset rows, experiment
artifacts, approvals and cloud receipts remain private; no deployment is implied.

## GPU runtime preparation

1. Fetch the exact NeMo revision in `config.json`, initialize its submodules and retain its
   `uv.lock`. Use NVIDIA's [container build instructions](https://docs.nvidia.com/nemo/rl/nightly/docker.html)
   with that revision as `NRL_GIT_REF`, or use the matching `nemo_image` digest in config.json.
   The current source pin was reviewed against the previous development revision: the text DPO
   preference preprocessing is unchanged; unrelated rollout and checkpoint I/O changes do not
   alter this local-checkpoint experiment. Build/download before
   allocating GPUs. Inspect the pinned source's `docs/docker.md` if the documentation moves.
   The service's [Dockerfile.writer](../Dockerfile.writer) adds this launcher to that image:
   build it with `--build-arg NEMO_IMAGE=<registry/image>@sha256:<digest>` using
   `src/model-training` as build context. Its entry point runs `writer_dpo.launch`; pass the
   launch arguments below to the container. No datasets or model weights are copied into it.
2. In that environment, with this service on `PYTHONPATH`, run
   `python -m writer_dpo.snapshot --output <absolute-path>/.local/qwen3-8b`.
   This explicitly downloads the pinned HF **full weights**, not the Ollama GGUF. It records
   hashes for the model and tokenizer. This command downloads large files; it was not run as
   part of CPU preparation. Keep the snapshot and bundle mounted read-only during training.
3. Mount the checked NeMo checkout, service source and local artifacts at stable absolute paths
   inside a dedicated GPU container. The checkout must retain its Git revision metadata.
   Execute the following from NeMo's environment, with service source on `PYTHONPATH`:

```sh
python -m writer_dpo.launch \
  --bundle /workspace/.local/writer-bundle \
  --nemo-root /opt/nemo-rl \
  --snapshot /workspace/.local/qwen3-8b \
  --output /workspace/.local/writer-smoke-01 \
  --mode smoke --execute
```

Without `--execute`, the command verifies pins and artifacts and writes configuration only.
With it, the actual NeMo tokenizer/data processor runs first: any zero-weight overlength
example aborts rather than silently disappearing from training. The GPU count and BF16
capability are checked before the upstream trainer starts. NeMo manages its workers through
Ray; do not wrap this entry point in a second `torchrun` launcher.

`--mode train` starts a **fresh** run in a new output directory after a successful smoke run;
it does not silently resume from the smoke checkpoint. The upstream trainer supports resume,
and `--mode restore-check` now provides a separate two-process functional verification.
It saves after step one, restarts from that checkpoint, verifies the selected weights and
optimizer plus the restored step/sample cursor, then requires one more step and changed
weight files. Both phases retain the same scheduler horizon and dataset. CPU contract
tests exercise failures as well as success; the four-GPU restoration check passed on the pinned runtime on 2026-11-10. This does not claim bitwise equivalence.
No automated topology sweep,
automatic resource provisioning is performed by the launcher.

## Vertex execution and artifact transport

[cloudbuild-snapshot.yaml](cloudbuild-snapshot.yaml) downloads and hashes full weights on CPU.
Its exact-object SDK uploads work with an object-prefix IAM grant; `storage.objects.list` is
not required. Both CLI `rsync` and CLI `cp` attempted bucket listing in the observed builds,
so the preparation now uses the SDK directly and publishes its manifest last. [cloudbuild.yaml](cloudbuild.yaml) builds the training image from a minimal source
context. Neither build requires a GPU. The Dockerfile verifies NeMo's source revision and pinned
interface hashes. The GCS SDK lives in a separate, hash-locked I/O environment so it cannot
change NeMo's training dependencies. Its installation uses `uv --no-config` to exclude
upstream project dependency overrides. Supply `_RUNTIME_MIRROR` to the image build: the
pinned base is copied to Artifact Registry with checksum-verified `crane` v0.22.1 before
custom layers. The copy must retain the source digest. This avoids Docker unpack/recompression
during mirroring, which exceeded an hour in an observed build. The application build then
uses the immutable mirrored reference. The build has a one-hour timeout.

[vertex.py](vertex.py) writes a REST CustomJob specification: one `a2-highgpu-4g` worker with
four A100 40 GB GPUs, 500 GB boot disk, FLEX_START scheduling, a 30-minute queue limit and
a two-hour job limit. It does not submit the job. FLEX_START uses the preemptible quota bucket
but is not a Spot-preemptible job. Quota availability does not prove physical capacity.

[cloud.py](cloud.py) downloads only manifest-listed inputs, checks their SHA-256 values, runs
the existing launcher, and periodically uploads output changes. The final `receipt.json` is
uploaded last after a full output sync. Partial logs/checkpoints survive normal process failure
or termination when shutdown time permits; abrupt VM loss may retain only the last periodic copy.
A successful process without a checkpoint is not marked complete. Completion does not establish
checkpoint restore or model quality; those require separate verification before accepting training.
Inputs and outputs must use separate prefixes, and a generation-precondition claim prevents
two jobs from silently sharing an output directory. Use a new prefix for every attempt.

The preference bundle is tracked by an ignored private DVC repository and uploaded to GCS.
If local DVC lacks Application Default Credentials, the authenticated Google CLI can transport
that exact DVC cache and pointer; record this fallback rather than claiming `dvc push` succeeded.
Training receives only train/validation JSONL and its manifest, never the holdout or review sheets.

The 2026-11-10 local review export contains 28 training pairs and seven unchanged validation
pairs. Three development captures were added to the previous 25-pair export: explicit Chinese
sleep-duration units, a sparse Chinese sleep-week summary, and five-metric source attribution.
Their captured prompts, evidence and rejected outputs are unchanged; the final chosen
completions have the owner's explicit confirmation. They are not independent test cases.
The final training file differs from the earlier 28-pair package in exactly those three
chosen completions; the validation JSONL is byte-identical. The new immutable GCS bundle
prefix and manifest hash are passed explicitly to Vertex, preventing use of the older package.
At global batch size four, an epoch has
seven complete batches; this is a constraint of the pinned loader, not a general requirement
that every dataset size be divisible by its batch size. The five previously blocked pairs
remain excluded. The private review workbook retains historical decisions and the exact
current exported completions alongside the readable review rows.

The process timeout is not a billing cap and does not stop a VM. Run in a dedicated disposable
container and set an independent VM/Vertex job lifetime. Container teardown must stop Ray
workers, including after failure. Record image digest, CUDA/driver versions, GPU names, logs,
checkpoint hashes and actual wall time in the run evidence. TensorBoard/GPU metrics are enabled;
W&B records offline by default. Set `WANDB_MODE=online`, `WANDB_ENTITY`, `WANDB_PROJECT`
and inject `WANDB_API_KEY` at runtime to upload. Never put the key in a job specification.
The Vertex generator accepts `--wandb-mode online --wandb-entity YOUR_ENTITY
--wandb-project YOUR_PROJECT --wandb-secret-version projects/PROJECT/secrets/SECRET/versions/1`;
only that pinned resource name enters the specification. The worker identity needs access
to that specific secret. Keys are read into process memory, not run records.

[tracking.py](tracking.py) records the destination, run ID, dependency/runtime versions,
configuration, bundle/model hashes and source hashes. The pinned upstream logger records
training/validation loss, optimizer and timing/GPU metrics; [nemo_entry.py](nemo_entry.py)
adds the HealthOps provenance record. [restore.py](restore.py) writes individual restore
checks; [cloud.py](cloud.py) sets `restore_verified` in the receipt only after a successful
restore check. A W&B connection-only preflight is not a training run or a restore test.
For cloud jobs, the unique GCS output URI supplies the W&B run identity. Separate jobs must
not merge into one run just because every container uses the same local output directory.
Restarting a job with the same output identity retains its W&B identity; local runs default
to their resolved output path. The cloud output-prefix claim still prevents concurrent reuse.

## Evaluation before and after training

The [20 frozen scenarios](../../api-service/eval/writer-holdout-v1/README.md) already exist in
code, with a manifest hash and conditional rubrics. Do not regenerate or tune on them. The
historical baseline reached valid writer inputs for 11 scenarios; nine were blocked upstream.
They must not acquire invented evidence just to increase the writer denominator.

Use two separate measurements:

1. **Writer replay:** identical 11 captured prompts, base versus adapted writer. Run the base
   again using the same serving stack, tokenizer and precision as the adapter. Historical
   Ollama Q4 results remain contextual, not a controlled BF16 fine-tuning comparison.
2. **Full-chain regression:** unchanged 20 scenarios / 22 turns, with the original router and
   tool model. The evaluation-only dispatch adapter replaces exactly the writer call.

From `src/api-service`, after an authorized OpenAI-compatible serving endpoint exists:

```sh
uv run python -m scripts.eval_writer_checkpoint \
  --model-url "$WRITER_URL" --model "$WRITER_NAME" \
  --checkpoint-identity "$CHECKPOINT_HASH" --serving-precision bfloat16 \
  --output .local/eval/writer-checkpoint-base
```

Repeat for the selected adapter, using a different output directory. The endpoint must honor
`chat_template_kwargs.enable_thinking=false`. This is a vLLM-style interface contract, not a
verified deployment. Export Megatron adapters using the pinned upstream conversion tool and
verify loading before serving; this preparation does not claim that conversion has run.

Each replay writes raw outputs, mechanical checks and **pending** semantic review labels.
Fill `semantic_verdict` (`pass`, `partial`, `fail`) and `review_reason` against the frozen rubric,
preferably blind to model identity. A JSON/checker pass is not answer-quality success.

```sh
uv run python -m scripts.compare_writer_checkpoints \
  .local/eval/writer-checkpoint-base .local/eval/writer-checkpoint-adapter
```

The comparison requires all 11 inputs, matching serving settings and complete reviews. Report
pass counts and case-level regressions; this tiny test does not establish broad generalization.

For the separate full-chain run, use the existing isolated synthetic `healthops_test` database
and importer described in the holdout README:

```sh
uv run python -m scripts.eval_writer_chain --holdout \
  --database-url "$HEALTHOPS_TEST_DATABASE_URL" \
  --import-dir .local/eval/writer-holdout-v1/import \
  --model-url "$BASE_OLLAMA_URL" --model-dir .local/reconcile/w14 \
  --writer-url "$WRITER_URL" --writer-model "$WRITER_NAME" \
  --writer-identity "$CHECKPOINT_HASH" \
  --output .local/eval/writer-holdout-v1/after-dpo
```

This changes the evaluation harness only, not the live API's model routing. Keep the serving
precision/runtime identical for paired full-chain comparisons too. Do not deploy a candidate
based only on validation loss or reuse these test failures as training data.

## Local verification

```sh
# In src/model-training:
uv run pytest -q tests/test_writer_dpo.py
# In src/api-service:
uv run pytest -q tests/test_writer_checkpoint.py tests/test_writer_holdout.py \
  tests/test_writer_chain.py tests/test_chain_preferences.py
```

Tests cover approval binding, trace identity, leakage, tampering, batch loss, configuration
limits, timeout behavior and writer-only dispatch. They use tiny synthetic fixtures and fake
models; no real health data, inference endpoint or paid GPU is contacted.

### Artifact synchronization failures

The cloud wrapper skips file and directory symlinks without following their targets.
Canonical files remain publishable; aliases are not checkpoint evidence. Provisional
sync failures are logged and retried on the next cycle without terminating training.
After the subprocess exits, all artifacts are refreshed with up to three attempts.
The final receipt is uploaded separately and last; `artifacts_synced=false` forces
`completed=false` and `restore_verified=false`. A fully unavailable storage service
can still prevent receipt publication, which makes the cloud job fail explicitly.

Fused linear log-probabilities require `overlap_param_gather=false` in Megatron's
`distributed_data_parallel_config`. All three execution modes set it explicitly;
the launcher checks it again after NeMo merges inherited defaults, before dataset
preflight or worker initialization. A duplicate output claim exits without overwriting
prior artifacts and reports the existing receipt status plus the original training-log
location. Missing or unreadable receipts do not authorize reuse of an output prefix.

### Post-training export and paired replay

[posttrain.py](posttrain.py) verifies the completed training receipt and selected checkpoint
file hashes, reconstructs the pinned base Megatron checkpoint, and calls the hash-pinned
upstream LoRA-to-Hugging-Face converter. It exports a PEFT adapter and checks that its tensors
are finite and include learned, nonzero LoRA B weights. These checks are not a numerical
round-trip equivalence proof.

The subsequent base/adapted replay uses the same Transformers SDPA stack, BF16 precision,
tokenizer, greedy generation, 800-token output limit and thinking-disabled chat template.
The 11 original captured test inputs and two frozen duration inputs are hash-checked;
inputs are never truncated. This is a separate controlled comparison from historical Ollama
Q4 results. Raw outputs retain pending semantic labels, and no test data enters training.
The worker uploads stage logs and a final receipt, with a 90-minute subprocess bound;
the submitted Vertex job must also have a resource lifetime limit. No endpoint is exposed
and this runner does not alter production routing or provision resources itself.

### Register an evaluated Writer version without deploying it

[registry.py](registry.py) adds a separate, evaluation-gated finalization step for the
fixed eleven-pair experiment. It does not change a running training job. Initialization
freezes the 48 validation inputs, 65 test inputs (20 final, 45 regression), candidate
checkpoint steps, data version, training configuration and bundle hashes.

```sh
# From src/model-training; EXPERIMENT is the local fixed-experiment directory.
python -m writer_dpo.registry initialize --experiment "$EXPERIMENT"
python -m writer_dpo.registry status --experiment "$EXPERIMENT"
# After reviewing all original-base and checkpoint validation responses:
python -m writer_dpo.registry select --experiment "$EXPERIMENT"
# After frozen final-test generation and semantic review:
python -m writer_dpo.registry finalize --experiment "$EXPERIMENT"          # local gates
python -m writer_dpo.registry finalize --experiment "$EXPERIMENT" --execute
```

Semantic review is explicit, not inferred from training loss or lexical checker acceptance.
`registry/validation-review.json` has `completed: true`, a nonempty `reviewer`, and `models`
mapping `base` and every candidate step string to per-case review arrays. Each row contains
`id`, `grade` (`pass`, `partial`, `fail`), nonnegative integer `unsupported_claims` and
`omitted_requested_facts`, `reason`, and the SHA-256 of the exact UTF-8 `raw_output` in
`output_sha256`. Raw responses are read from `training/observed-output/`; each must match
the frozen input hash, and missing, duplicate, empty or possibly truncated outputs block
registration. The existing cloud fetch command retrieves these validation artifacts.

Selection minimizes failed cases, unsupported claims, then omitted requested facts on
47 semantic validation questions. Engineering-only V15 is reported separately. Ties prefer
the original base, then the earlier checkpoint. Selecting the base stops registration.
`selection.json` and `final-test-plan.json` are immutable. Recompute checks prevent changing
the selected checkpoint in response to final-test results.

[registry_replay.py](registry_replay.py) produces the receipt required by finalization on
an **already allocated** GPU, after validation selection:

```sh
python -m writer_dpo.registry_replay --experiment "$EXPERIMENT" \
  --snapshot "$PINNED_BASE_DIRECTORY" --adapter "$SELECTED_ADAPTER_DIRECTORY"
```

It verifies the full base snapshot and selected adapter hashes, then generates the 65
frozen questions with original base and selected adapter, using BF16, SDPA, greedy decoding,
thinking disabled and an 800-token output bound. It neither allocates GPUs nor selects a
checkpoint. Partial runs are preserved and cannot silently overwrite earlier generations.
The experiment inputs and selected artifacts must be staged in that environment first.

`registry/final-test-review.json` uses the same review-row format, `models` keys `base` and
`selected`, and the exact `selection_sha256`. `generation-receipt.json` binds the response
files to the frozen test plan, base snapshot, training bundle and adapter export check.
The 20 final cases must have no failed answers, unsupported claims or per-case degradation
against base; partial answers are allowed only without degradation. All 45 legacy cases
must be reviewed and are retained as report-only regression results. These are explicit
experiment acceptance rules, not a claim of clinical validity or production readiness.

Finalization verifies the successful CustomJob, remote training receipt, original validation
outputs, state audit, snapshot manifest and adapter bytes. It writes a content-addressed GCS
release containing the adapter, pinned base reference, training config, dataset manifest,
checkpoint/state references, raw paired evaluations, reviews and summary. It then uploads a
referenced Model version under `healthops-writer-qwen3-8b`. The registry version records an
immutable release alias and metadata pointing to that release. The first Model receives a
Vertex-assigned default version; subsequent uploads do not move the default alias.

There is deliberately no serving container or `artifactUri` on the Model resource: the
base-plus-adapter release URI is stored in metadata. This follows the SDK's
[referenced-model upload path](https://github.com/googleapis/python-aiplatform/blob/main/google/cloud/aiplatform/models.py).
Weights remain in GCS and need a separately verified serving configuration before deployment.
The registry client permits Model upload and read-only operation/model/job lookups; it has
no endpoint or deployment methods. It does not change production routing or IAM.

GCS generation preconditions prevent overwrites and concurrent duplicate submission. An
existing alias is reused, a saved operation is resumed, and an upload with an unknown outcome
is not blindly retried. Run the same finalization command to inspect a pending operation.
The caller needs existing GCS read/write and Vertex model-upload permissions; credentials
come from `gcloud auth print-access-token` and are never printed or archived.

Local tests use synthetic responses and a fake cloud transport, including successful
registration, failed/incomplete review, tampering, wrong checkpoint/model, duplicate and
ambiguous uploads, and forbidden deployment calls. GPU final replay and real registry upload
remain unverified until a candidate completes evaluation; no dummy passing model is uploaded
to test the integration.
