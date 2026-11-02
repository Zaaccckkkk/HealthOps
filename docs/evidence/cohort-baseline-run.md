# Qwen3-8B three-account diagnostic baseline

Observed on 2026-10-26, one sequential local run of 30 cases, ten intentions per synthetic
account. The v2 set corrected provider request scoping before inference; no prompt/model changes
or score-driven reruns were made. This is a development diagnostic set, not held-out accuracy.

## Conditions

- Qwen3:8b, GGUF Q4_K_M, Ollama 0.35.0; digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Prompt `2026-10-17.1`, content/schema/source hashes checked before and after execution.
- Temperature 0, thinking false, maximum output 800 tokens; concurrency 1, context 32768.
  Actual loaded context/digest checked through `/api/ps` after an unscored empty-prompt warmup.
- Local Mac Ollama, in-process application orchestrator, read-only local PostgreSQL connection.
  Authentication/network/UI performance is outside this run's scope.
- Synthetic identity mapping and all 21 deterministic gold outputs verified before and after.
  A separate Docker source-hash check confirmed running API assistant/tool/config files match
  the frozen local hashes. No records were written and no cloud resources were changed.
- RAG cases measure routing only, with knowledge deliberately unavailable; they do not evaluate
  retrieval or reference answer quality.

## Observed results

| Metric | Passed / denominator | Interpretation |
|---|---:|---|
| First tool matches expected | 27 / 30 | Three missing-window cases first called availability |
| Original arguments match expected | 27 / 30 | Includes matching tool and topic checks for RAG |
| Executed arguments match expected | 24 / 27 | RAG free-text query cases excluded |
| Data evidence matches frozen gold | 21 / 21 | Correct evidence appeared somewhere in each data trace |
| Refusal category and status | 6 / 6 | Medical advice and other-person requests |
| Verified data explanations | 13 / 21 | Supported claims, not a completeness score |
| RAG first-tool/topic routing | 3 / 3 | Final RAG answers excluded from grading |

The three incorrect first-tool cases eventually called the expected daily_summary correctly;
this is extra routing, not failure to retrieve the data. No single end-to-end accuracy is claimed.
All three unavailable-knowledge cases exhausted the tool budget after repeated retrieval; this
is behavior in the routing-only test configuration, not evidence that the application's corpus
is missing. Raw outputs remain in `.local/eval/cohort-baseline-run-v2/`.

## Error analysis

1. **Availability explanation (3 cases):** correct tool and data, but state_findings cites an
   unknown evidence ID. There is also a protocol design limitation: a nonempty availability
   card exposes no supported summary claim, while findings require at least one entry. Do not
   label all of this as a model training problem. Design an explicit availability rendering path.
2. **Empty-window explanation (3 cases):** an extra availability call precedes daily_summary;
   missing_data then references an invented `e1.healthkit` ID. The verifier correctly rejects it.
   Training/evaluation should emphasize selecting IDs actually listed in the current evidence.
3. **Tool-call serialization (2 paired-account cases):** after correct heart-rate/comparison
   retrieval, the model writes `state_findings` and JSON in ordinary message content rather than
   native tool_calls. The application does not execute that text and shows its fallback. The
   recorded responses ended normally, below the 800-token cap; these were not truncation errors.
4. **Completeness (1 verified Fitbit heart-rate case):** six recorded days are correctly cited,
   but the missing seventh day is omitted despite a question requesting each day. Verification
   proves support for claims made, not complete coverage of the user's question.

Prioritize fixing the availability contract, then measure protocol serialization and evidence-ID
selection with a versioned prompt/adapter experiment. Preserve this run as the unchanged baseline.
Fine-tuning can target observed residual failures after those changes; these results alone do not
justify a particular training method or predict its improvement.

## Verification

Preparation/runner and existing evaluation-set tests: 18 passed (5 new cases and 13 existing),
with one upstream Starlette/httpx deprecation warning. Tests distinguish server repair from raw
model correctness and reject altered tool evidence, in addition to date/schema/hash checks.
Lint, formatting, public-content and Markdown-link checks passed. Full integration and iOS/data
pipeline suites were not rerun: this change adds evaluation scripts and does not change runtime
application behavior. The real-model run above separately exercised the actual local data path.
