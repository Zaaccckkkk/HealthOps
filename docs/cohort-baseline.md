# Three-account diagnostic baseline preparation

Status (2026-10-26): 30 cases prepared and local deterministic gold outputs collected.
The v2 format corrects provider scoping before inference; v1 is retained locally for audit.
The 30-question model run is now complete; see [observed results](evidence/cohort-baseline-run.md). This is a development diagnostic
set, not an independent held-out test. Ten intentions are repeated across three synthetic
accounts; the 30 cases are not 30 independent reasoning patterns.

## Reproduce

Prerequisites: local API on port 8000, Ollama on 11434, Docker access, and the completed
[synthetic import](synthetic-import.md). Run from the repository root:

```bash
cd src/api-service
uv run python scripts/prepare_cohort_baseline.py \
  --directory .local/synthetic-import/synthetic-v0.1 \
  --output .local/eval/cohort-baseline-v2
```

An existing output directory is never overwritten. To validate the existing snapshot, use the
same command with `--verify`. A new snapshot requires a new output directory. The script reads
only the three imported synthetic identities, checks `/v1/me`, and fetches authenticated tool
results; it never invokes `/v1/assistant/ask` or Ollama inference. Loopback addresses are fixed
and ambient HTTP proxy settings are disabled. Tokens are read locally but never written to the
question set or lock. Do not publish the generated data or local credential files.

## Artifacts and freeze contract

The ignored `.local/eval/cohort-baseline-v2/` directory contains:

- `questions.jsonl`: account mapping, question, fixed timezone/today, expected call and gold result.
- `lock.json`: model digest, Ollama version, quantization, model template hash, original model
  parameters, observed API settings, dataset manifest hash and artifact/source hashes.
- `system-prompt.txt` and `tool-schemas.json`: exact prompt template and model-facing tool schemas.

The installed model is `qwen3:8b`, GGUF Q4_K_M, Ollama 0.35.0. Its recorded digest is
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
The prompt version is `2026-10-17.1`; the content hash, not only the version label, is checked.
The API uses temperature 0, thinking false and 800 maximum output tokens. The model's saved
parameter text includes temperature 0.6; the API request overrides that with 0. Do not confuse
model defaults with effective request settings.

`--verify` rejects changes to model metadata, observed API settings, local assistant/tool code,
prompt, schemas or generated artifacts. It does not freeze the running database, revalidate gold
against a changed DB, or prove that the running API image equals the local source checkout.
The future runner must verify those inputs before execution. Keep this lock for comparison;
updating a tag or prompt requires an explicitly new run definition, not silently replacing it.

Evaluation requires sequential requests, the per-question `today` override and `num_ctx=32768`.
No model was loaded at preparation time, so that context length is a required future evaluation
setting, not an observed runtime measurement. The future runner must pass/verify it explicitly.
Temperature zero alone does not guarantee bit-for-bit deterministic generation.

## Case design and gold

Each account has seven data cases: availability, steps, heart rate, request-provider-filtered steps,
period comparison, an empty future window, and yesterday. The other three cases cover reference
routing, medical-advice refusal and another-person refusal. Total: 21 data golds and 9 routing
expectations. Some additional data windows naturally lack the requested metric; this is valid.

`today` is the day after each account's last recorded date. Thus "yesterday" resolves to that
account's last date even when evaluation happens months later. The rendered system prompt hash
is stored per question. Timezone and provider are server-injected, not expected model-supplied arguments.
Provider cases carry `request_provider` and separate `expected.model_arguments` from the
executed arguments. A channel named only in question text would trigger clarification.

Data golds are full responses from the existing deterministic HTTP tools, not text invented by
a language model. They are application regression oracles, not independently recomputed truth;
a shared tool bug could affect both gold and evaluation. Existing tool unit tests remain useful.
An eventual response grader must check fields and warnings semantically, not compare prose
verbatim. Null means missing; interval totals must not be described as complete-day totals.

Reference cases expect `lookup_reference` with the SDNN/RMSSD topic, not an exact query string.
This round does not grade retrieved passages or reference answers, because the retrieval corpus
and embedding configuration have not been frozen. Refusal cases require `decline_request` with
the appropriate category. Do not add tool identity fields to model arguments.

## Preparation verification

`tests/test_cohort_baseline.py` validates ten distinct cases per cohort, schema compatibility,
provider selection, adjacent comparison windows, relative-date resolution and tamper rejection.
Together with `tests/test_eval_sets.py`: 17 passed (4 new cases, 13 existing), one upstream
Starlette/httpx deprecation warning. The actual preparation made 21 successful tool requests;
the three intentionally empty windows had zero records. `--verify` passed against local Ollama
and the observed API settings. No cloud changes, database writes or model inference were needed.

## Run the cohort baseline

The existing `eval_assistant.py` still seeds its own fixture user. The new runner explicitly
consumes this frozen format, verifies identity mapping and recomputes all 21 golds against a
read-only local PostgreSQL connection before and after inference:

```bash
cd src/api-service
uv run python scripts/run_cohort_baseline.py \
  --dataset .local/eval/cohort-baseline-v2 \
  --output .local/eval/cohort-baseline-run-v2
```

The runner calls the existing orchestrator in process, rather than the HTTP API: this permits
the fixed evaluation date without changing the live application's settings. This measures the
assistant, not authentication, network latency or UI behavior. It enforces concurrency 1 and
injects `num_ctx=32768` into every Ollama chat request using a transport adapter. Before scoring,
an empty-prompt load warms the model and `/api/ps` must confirm the digest and context length.
Reference cases deliberately receive an unavailable knowledge context; only routing is scored.
No retrieval-quality claim can be inferred from these cases.

Per-question files retain raw model requests/responses, tool traces, cards, verified claims and
grades locally. Partial results are written after each question. `run.json` is only marked
completed after the final model/artifact and database checks pass. Existing output directories
are never overwritten. An interrupted run must not be reported as a completed baseline.

Scoring separates first-tool selection, exact original model arguments, executed arguments,
exact matching tool evidence, explanation verification and refusal correctness. Model-supplied
hidden provider/timezone fields count as raw-argument errors even if the server repairs them.
`explanation_status=verified` only means claims are supported, not that the response fully answers
the question. Human review must check missing providers/days, relevance and completeness.
