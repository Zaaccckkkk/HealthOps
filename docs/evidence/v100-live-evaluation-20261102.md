# V100 recovery and live assistant evaluation — 2026-11-02 UTC

## Runtime and reproducibility

A temporary `n1-standard-4` with one V100 was allocated in `us-central1-a` after
`us-east1-b` and `us-east1-c` returned `configuration_availability` under
`ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS`. These are allocation outcomes, not evidence that
all V100 capacity in a region is unavailable. Only one temporary GPU VM was running, with a
7,200-second automatic STOP and automatic restart disabled. The existing personal-history VM
and dedicated L4 VM remained stopped.

The clean image was `common-cu129-ubuntu-2204-nvidia-580-v20261029`. First boot took roughly
145 seconds. Its open NVIDIA kernel module rejected V100 because the device lacks GSP support.
Replacing that module with the proprietary 580 module for kernel `6.8.0-1070-gcp` restored
`nvidia-smi`: Tesla V100-SXM2-16GB, driver 580.178.04, 16,384 MiB.

Ollama initially fell back to CPU despite host GPU detection. Explicitly selecting its bundled
CUDA 12 backend resolved discovery. The checked-in [V100 override](../../src/deployment/cloud-run/compose.v100.yml)
and [driver runbook](../../src/deployment/cloud-run/README.md#temporary-v100-evaluation-fallback)
record the working configuration. Deployment configuration commit: `487eff9`. No assistant
algorithm, prompt, evaluation rubric, corpus, health observations or database migrations changed.

- Ollama: 0.35.1; Qwen3-8B Q4_K_M; temperature 0; thinking disabled; context 32,768;
  maximum model output 800 tokens; one parallel request and one loaded model.
- Qwen digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Embedding digest: `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.
- Both digests matched the previous frozen runtime. Logs confirmed 37/37 layers offloaded;
  `/api/ps` reported `size_vram == size == 9969650892` and context length 32,768.
- First GPU warmup took 80.5 seconds including cold model loading. This is not steady-state
  answer latency or a performance comparison with L4.
- API implementation: `89c2d36`; image
  `us-east4-docker.pkg.dev/example-healthops-project/healthops/api@sha256:a0270541d0f8bc4dd74a9ac033a3cb7ebe1e254089f2ea798bf94814dcd36c54`.
- Evaluation revision: `healthops-ms3-api-v100-eval-1102`, IAM protected, zero normal traffic,
  frozen-date overrides enabled only for the evaluation revision. FastAPI/LangGraph ran on
  Cloud Run; Qwen and embeddings ran on the V100 VM; data remained in synthetic Cloud SQL.

The runner verified exactly three synthetic accounts and compared deterministic tool outputs
against the frozen cohort. Each HTTP identity was checked through `/v1/me`. The existing
per-account rate limit remained enabled; the runner honored HTTP 429 retry delays. No ambiguous
transport request or consumed continuation was automatically replayed.

## Frozen evaluation inputs

| Input | SHA256 |
| --- | --- |
| 63-scenario diagnostic set | `1c9a41cfbf3775d0cfc4e4e7f30ad857aabe3c296336792117da0b56480d7110` |
| 36-scenario interaction set | `c155a9ec63c62b50728a38fe4788fba0f1b63b1a5bfec01378303362208c1f84` |
| Synthetic cohort lock | `a1249f63e2c5d6ff7c61287214ebab624e1340f42d5e8f3d963c9174b09e56b4` |

Raw responses, gold, credentials and runtime artifacts remain ignored under
`src/api-service/.local/eval/` and `.local/cloud/ms3/`. They are not publication artifacts.

## Completed diagnostic results

Smoke: 4/4 scenarios, 5 turns, covering all three accounts and clarification/resumption.
Full diagnostic: 62/63 scenarios, 87 turns; 59/60 end-to-end scenarios and 3/3 routing-only
reference scenarios. Router status matched on 87/87 turns; all 48 ready turns selected the
required primary tool. The 45 non-reference execution turns matched the independently computed
gold evidence and requested execution scope. Verified claims and complete coverage passed on
44/45 of those turns. All 24 clarification turns exposed the required continuation and missing
fields. These are observations on the fixed diagnostic set, not held-out production accuracy.

The failure was `routed-v1-00003-usual`:

1. "Is my sleep duration different from usual?" correctly requested the current period and
   comparison baseline without running tools.
2. "Compare the past 7 days with the previous 7 days." correctly resolved the requested windows
   and ran `period_comparison` for `sleep_duration`.
3. A comparison evidence card was available, but the model returned an empty answer before
   submitting verified findings. The response was `model_error`, with no claims or verification
   attempts and an unreported coverage target. It failed the rubric; it was not a connection
   failure or an incorrect router decision. Root cause of the empty model output is not yet proven.

The three SDNN reference questions selected `lookup_reference` with query `SDNN` but returned
no matching passages and explicit `insufficient_data`. The synthetic database contains 570
reference chunks, so the library is not empty. Routing-only PASS does not establish retrieval
recall, citation quality or a successful RAG answer. No similarity threshold was lowered.

## Interaction results and diagnostic repeats

Interaction suite: 32/36 scenarios, all 72 planned turns executed. Route status matched on
72/72 turns and all 36 clarification turns supplied continuation state and the required missing
fields. This three-way routing result does not mean every intent/tool was correct: request-spec
and primary-tool checks passed on 27/30 ready turns. Gold evidence matched on 27/30; coverage
against the independently authored specification passed on 26/30.

Four interaction scenarios failed:

- `interaction-v1-00000-no_record`, `interaction-v1-00003-no_record`, and
  `interaction-v1-00001-no_record`: "Any steps recorded on 2020-02-02? Please distinguish missing
  from zero." selected `availability`, while the fixed expected contract required a `summary`
  request and metric-specific `daily_summary` evidence. The output correctly said no recorded
  series, without inventing a zero. These are intent/tool/coverage contract mismatches, not
  observed numerical hallucinations. The wording plausibly asks about availability; manual
  adjudication should determine whether an alternative valid path belongs in a future rubric.
  The current rubric and original failed scores were not changed.
- `interaction-v1-00003-correction`: after an initial vague request, the user clarified
  "steps, sleep_duration; past 7 days; Additional details or correction: Actually steps only".
  The router correctly retained only steps and the intended window. The summary tool succeeded,
  but the model returned an empty answer before findings/verification, as in the diagnostic
  failure above.

Both empty-answer scenarios were repeated once in separate fresh HTTP conversations. Both
reproduced the empty-answer failure. These two diagnostic repeats are stored separately and do
not replace the original results. No root-cause claim about GPU hardware, token limits, prompt,
model weights or Ollama parsing is established by this observation.

Across the two original suites: 94/99 scenarios passed, including 91/96 end-to-end and 3/3
routing-only reference scenarios. All 159 planned turns ran. The four-scenario smoke run and two
failure repeats are excluded from these totals. This is a fixed-set diagnostic result, not a
production accuracy estimate or evidence of universal security.

## Retrieval follow-up

A read-only diagnostic repeated embedding of the actual tool query `SDNN` with the unchanged
embedding model and searched the same synthetic reference table. Top-three cosine similarities
were 0.491643, 0.447680 and 0.432555. All were below the configured 0.55 threshold. The table
contained 570 chunks under `nomic-embed-text`. This explains the empty retrieval response for
this query; it does not establish corpus completeness or justify lowering the threshold without
relevance evaluation. No source text, embeddings or threshold was modified.

## Follow-up priorities

1. Capture a bounded, private raw model-response diagnostic for the reproducible empty-answer
   cases, including finish reason and token usage. Distinguish model EOS, truncation and serving
   parser behavior before changing prompts or training.
2. Adjudicate the availability-versus-summary wording against the intended product contract;
   preserve metric-specific missing-versus-zero coverage if allowing multiple tool paths.
3. Evaluate retrieval relevance and recall on labeled reference questions. Keep routing success
   separate from citation and answer quality.

## Verification limits and costs

Compose configuration validation passed; repository/deployment tests passed 25/25. The API
unit/integration suite was not rerun for this deployment-only override; its previous result is
recorded in [connection-timeout evidence](model-connect-timeout-20261101.md). Real HTTP model
results above are separate from those local tests. Native iOS cloud acceptance and broad
prompt-injection robustness are not established by these QA runs.

The official Iowa on-demand V100 GPU price is $2.48/GPU-hour, excluding VM CPU/RAM, disks and
networking. Approximately $6 for a two-hour single-VM test was a planning estimate, not a billing
measurement or a total-project spending cap. Existing Cloud SQL and stopped-VM disks continue
billing independently. See [GPU prices](https://cloud.google.com/products/compute/gpus-pricing?hl=en)
and [Ollama 0.35.1 hardware support](https://github.com/ollama/ollama/blob/v0.35.1/docs/gpu.mdx).


## Cleanup

Cleanup began approximately 32.86 minutes after the temporary VM's recorded start time. That
interval is not a final billing measurement. Final resource queries confirmed the temporary VM
and boot disk were deleted, both original GPU VMs remained TERMINATED, and the IAP tunnel closed.
The `v100-eval` tag and frozen-date revision were deleted. Zero-traffic candidate
`healthops-ms3-api-connect-verified-1102` retains the same API image under `safety-candidate`,
with date overrides disabled and the original private L4 endpoint restored. Normal traffic
remains 100% on `healthops-ms3-api-00002-ps7`; the pre-existing routed candidate was preserved.
No production promotion occurred. Because the temporary V100 was deleted and the original L4
is stopped, this cleanup does not leave a running model service for ongoing app use.
