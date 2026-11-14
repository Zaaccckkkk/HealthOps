# Portfolio evaluation results

The project owner supplied and confirmed these aggregate results on November 13, 2026,
reporting that a separate evaluation agent had verified them:

| Evaluation | Sample size | Measure | Result |
| --- | ---: | --- | ---: |
| End-to-end live scenarios | 93 scenarios | End-to-end task success | 86.0% |
| Base Qwen3-8B offline answers | 112 cases | Answer acceptance | 80.4% |
| DPO-fine-tuned Qwen3-8B offline answers | 112 cases | Answer acceptance | 91.1% |

The offline comparison improves answer acceptance by 10.7 percentage points. The live
scenario result is separate; it should not be interpreted as the offline acceptance rate
or as a measured DPO uplift on live scenarios.

## Source and reproduction boundary

These are owner-confirmed aggregate results, not results recomputed during the portfolio
synchronization. The corresponding per-case reports, acceptance rubric, run configuration
and checkpoint identifiers were not supplied for publication with this update. No integer
pass counts, held-out-test claim, confidence interval or specific live model configuration
is inferred from the rounded percentages.

Historical evidence files keep their original datasets, dates and definitions. In particular,
the November 2 diagnostic report and the local Writer inventory are not the source of the
new aggregate. The public quickstart demonstrates the implementation; reproducing these
scores requires the matching frozen inputs, evaluation criteria and model artifacts.

The [synchronization verification](portfolio-sync-20261113.md) covers software regressions
and publication checks. It is separate from these model-quality results.
