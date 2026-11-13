# Development model comparison

This comparison separates full-chain behavior from answer-writing behavior. It
does not use personal health records or the frozen held-out evaluation sets.

## Protocol

- Full chain: 30 pretraining scenarios plus six three- and five-metric
  clarification scenarios across three synthetic v0.2 accounts. The runner uses
  the real API through ASGI, isolated PostgreSQL, deterministic tools, routing,
  evidence checks, and the answer writer. This is not a mobile transport test.
- Writer replay: 25 approved training preference prompts and nine Chinese sleep
  development captures. Exact prompts and evidence are frozen and hashed. Chosen
  reference answers are available to the reviewer, never to the evaluated model.
- Qwen3-8B and Qwen3-14B use Q4_K_M, temperature zero, and disabled thinking.
  Runs record model digests, context settings and source hashes. Concurrent
  activity and hardware differences prevent a controlled latency comparison.
- The planned Qwen3-30B-A3B Instruct comparison is blocked by L4 availability.
  No MoE response or accuracy is claimed. The instruction-tuned MoE release also
  differs in post-training, so this is not a controlled architecture ablation.

`scripts/eval_model_sensitivity.py` replays the writer inputs. It applies the
existing single-check, optional single-editor contract. Parsing success does
not establish factual correctness. `scripts/eval_writer_chain.py --pretraining`
and `--multibudget` capture the full chain and independent stage checks.

The local review workbook retains question IDs, user clarification turns, model
answers, initial drafts, checker feedback, original evidence, source files and
separate human-review fields. An automatic stage result and a semantic review
are distinct columns. Unexecuted cases are not counted as incorrect answers.
There are 70 scenario entries per candidate, with deliberate overlap between
suites; this is not a 70-question independent held-out benchmark.

## Verification and limitations

- 157 targeted assistant, duration, context and guard tests passed.
- Six context and comparison-loader tests passed after the final context change.
- 42 writer DPO, cloud contract and tracking tests passed, including restore
  receipt behavior. These tests do not establish successful GPU training.
- Online W&B connectivity was verified with a marker run, not a training run.
- The complete model-training suite aborts in the local native LightGBM runtime;
  the targeted DPO tests pass. No complete-suite success is claimed.
- Functional checkpoint resume verification is implemented but still requires
  execution on the intended four-GPU NeMo environment.
- No phone API revision was deployed as part of this comparison.

Raw outputs, model responses, manifests and the editable workbook remain local.
Do not use the frozen validation/test sets to select prompts or candidates.
