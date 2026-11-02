# Cloud LangGraph evaluation: 30 baseline questions and six exploratory probes

Observed 2026-10-30. All calls used the three frozen synthetic accounts. No personal records were queried.

## Execution and comparison limits

The new API image contains LangGraph 1.2.18. Cloud Build `8bc8fdf5-88bf-444e-8b42-5ebff4960819` produced image digest `sha256:746fdfe1a23d8d5674346b04ee6210d5092c848a7d7ec389c0c0dc0f76968e4a`.

Normal service revision: `healthops-ms3-api-00002-ps7`. The temporary IAM-protected `healthops-ms3-api-00003-bet` revision accepted frozen evaluation dates and received no default traffic. It used the same image and data. After evaluation, its tag and revision were removed; the normal service retains date overrides disabled.

Qwen3-8B Q4_K_M digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, Ollama 0.35.1, NVIDIA L4, observed context 32768, temperature 0, thinking false. Questions, golds, tool schemas and prompt hashes were checked against frozen v2 artifacts. The runner reused the existing `grade()` function without changing scoring.

The old run used a local model server and routing-only RAG with no corpus. This run used Cloud Run HTTP and enabled the cloud reference corpus. Hardware/server version and retrieval conditions differ. This is a deployment rerun, not a controlled estimate of the effect of LangGraph.

## Results

| Metric | Previous local run | Cloud LangGraph run |
| --- | --- | --- |
| First tool matches gold | 27/30 | 29/30 |
| Raw arguments match | 27/30 | 29/30 |
| Executed arguments match | 24/27 | 26/27 |
| Deterministic evidence matches gold | 21/21 | 21/21 |
| Refusals match expectations | 6/6 | 6/6 |
| Verified explanations among 24 non-refusal cases | 13/24 | 17/24 |
| Median request latency | 5.05 s | 2.408 s |

These are separate diagnostics, not a single answer-accuracy score. Evidence matching can pass even if the first tool differs or the explanation fails. The six exploratory questions do not have a single gold tool or an automated pass/fail score.

## All 30 baseline outcomes

| Case | First tool correct | Gold evidence | Explanation |
| --- | --- | --- | --- |
| synthetic-00000-availability | yes | match | unverified |
| synthetic-00000-steps | yes | match | verified |
| synthetic-00000-heart_rate | yes | match | verified |
| synthetic-00000-provider | yes | match | verified |
| synthetic-00000-comparison | yes | match | unverified |
| synthetic-00000-missing | yes | match | verified |
| synthetic-00000-relative_date | yes | match | verified |
| synthetic-00000-reference_route | yes | n/a | verified |
| synthetic-00000-medical_refusal | yes | n/a | not_applicable |
| synthetic-00000-identity_refusal | yes | n/a | not_applicable |
| synthetic-00003-availability | yes | match | unverified |
| synthetic-00003-steps | yes | match | verified |
| synthetic-00003-heart_rate | yes | match | verified |
| synthetic-00003-provider | yes | match | verified |
| synthetic-00003-comparison | yes | match | verified |
| synthetic-00003-missing | yes | match | verified |
| synthetic-00003-relative_date | yes | match | verified |
| synthetic-00003-reference_route | yes | n/a | verified |
| synthetic-00003-medical_refusal | yes | n/a | not_applicable |
| synthetic-00003-identity_refusal | yes | n/a | not_applicable |
| synthetic-00001-availability | yes | match | unverified |
| synthetic-00001-steps | yes | match | verified |
| synthetic-00001-heart_rate | yes | match | unverified |
| synthetic-00001-provider | yes | match | verified |
| synthetic-00001-comparison | yes | match | unverified |
| synthetic-00001-missing | no | match | unverified |
| synthetic-00001-relative_date | yes | match | verified |
| synthetic-00001-reference_route | yes | n/a | verified |
| synthetic-00001-medical_refusal | yes | n/a | not_applicable |
| synthetic-00001-identity_refusal | yes | n/a | not_applicable |

## Failure examples

- Paired-account missing-data case: expected `daily_summary`, but first selected `availability`. It later retrieved matching gold data, then submitted an unknown evidence ID. The explanation remained unverified.
- Paired-account heart-rate and comparison cases: tools ran successfully, but the model ended without structured `state_findings`. Cards remained available; the server displayed the fallback explanation.
- Availability cases returned useful cards but no verified final explanation. This should be distinguished from wrong tool data.

## Six exploratory questions

All six returned `answered` and `verified`. Manual review still found the following limits:

| Probe | Observed behavior | Remaining issue |
| --- | --- | --- |
| How did I sleep last night? | Sleep duration for the previous day, both providers | Duration alone does not establish overall sleep quality |
| Chinese equivalent | Correct previous-day duration, English final template | Renderer does not match the requested language |
| Sleep duration and resting heart rate yesterday | Queries both, cites only sleep | Omits the requested metric with no records |
| Better sleep last night than usual? | Attempts overlapping windows, then compares two longer windows | Does not answer last night versus an agreed usual baseline |
| How have I been doing lately? | Seven-day heart rate, steps and sleep queries, then 15 findings | Unannounced scope, long fact list, incomplete provider/date coverage |
| Was tiredness caused by poor sleep? | Compares yesterday with today, repeats insufficient-data statement | Does not explain the causal limitation or answer usefully |

## Post-training implications

Review failures before creating training examples. Correct tool selection, valid evidence references, complete requested metrics and suitable time windows are candidate SFT targets. DPO additionally needs reviewed chosen/rejected trajectories. Online RL requires a validated reward and rollout process. English-only templates and missing coverage checks also need application changes; fine-tuning alone does not fix them.

These 36 questions are a development diagnostic set. If their failures inform training, they cannot serve as an untouched final test set. Split future data by synthetic person and question family, and keep a separate holdout. Training has not started.

## Reproduction

Use `src/deployment/cloud-run/evaluate_http.py` with the frozen v2 set, original natural-probe JSONL, private synthetic credentials and a temporary protected evaluation revision. Raw results, exact questions, run hashes and the normal-service smoke result remain in ignored `src/api-service/.local/eval/cloud-langgraph-20261030/`. Do not publish credentials or raw local artifacts.
