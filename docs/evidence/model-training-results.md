# Model training results: cross-device reconciliation on synthetic-v0.2

Observed on Vertex AI, 2026-10-31. Three custom jobs on `n1-highcpu-16` (CPU only), data read from the
team bucket, outputs in `gs://ac215-project-509705-healthops-data/training/runs/<job>/`, W&B project
`healthops-m3`. All data is synthetic; ground truth is the synthesizer's own.

| Job | Purpose | Result |
|---|---|---|
| `healthops-train-synthetic-v0.2-20261031T213002Z` | Smoke, 200 users, tiny settings | Pipeline check only; numbers not meaningful |
| `healthops-train-synthetic-v0.2-20261031T213326Z` | Full data, LightGBM only | Identical to the LightGBM rows below |
| `healthops-train-synthetic-v0.2-20261031T220818Z` | Full data, all three models | Tables below |

Cohort: 5,000 users split by user-ID hash into 3,494 train / 750 validation / 756 test. Test rows:
35,116 sleep nights, 42,787 step days, 40,354 resting-heart-rate days.

## 1. Model comparison

### What each measure means

- **MAE**: mean absolute error of the estimate against the synthetic truth, in the metric's unit. Lower is better.
- **Coverage 90**: share of days whose truth falls inside the model's 90 % interval. The target is 0.90; below means over-confident, well above means the intervals are wider than needed.
- **Width 90**: average width of that interval. Narrower is better at the same coverage.
- **Attribution**: on days where the two devices disagree, how often the estimate moves away from the device with the larger true error. 0.50 is a coin flip.
- Rule baselines use no learning: trust one device, average both, or subtract one global constant per device.

### Accuracy (MAE, held-out test users)

| Model | Sleep (min) | Steps | Resting HR (bpm) |
|---|---|---|---|
| Device average (rule) | 18.6 | 1,209 | 1.69 |
| Global correction (rule) | 13.0 | 1,251 | 1.21 |
| **LightGBM** | **11.6** | 1,032 | 1.19 |
| **Transformer** | 12.2 | **998** | **1.17** |
| Diffusion | 30.8 | 1,682 | 1.93 |

### Calibration (90 % interval)

| Model | Sleep coverage / width | Steps coverage / width | RHR coverage / width |
|---|---|---|---|
| **LightGBM** | **0.90** / 51 min | **0.89** / 4,320 | **0.89** / 4.8 bpm |
| Transformer | 0.93 / 49 min | 0.90 / 4,152 | 0.92 / 5.2 bpm |
| Diffusion | 0.99 / 256 min | 0.98 / 12,263 | 0.98 / 13.0 bpm |

### Attribution (which device to trust on disagreeing days)

| Model | Sleep | Steps | Resting HR |
|---|---|---|---|
| Best rule | 0.84 | 0.53 | 0.89 |
| **LightGBM** | 0.86 | **0.74** | 0.88 |
| **Transformer** | 0.84 | **0.75** | 0.89 |
| Diffusion | 0.58 | 0.59 | 0.78 |

### Interpretation

- LightGBM and the transformer are equal within noise and beat every rule baseline on all three metrics.
- LightGBM is the best calibrated: coverage sits on the 0.90 target. The transformer is slightly cautious (0.92 to 0.93).
- The diffusion model is not competitive at this CPU budget: worse than the rules on sleep and steps, intervals five times wider, attribution near chance.
- Steps and sleep are where learning pays. Resting heart rate is already solved by subtracting a constant, because the synthetic device bias there is mostly a fixed offset.
- Sleep and resting heart rate are at the noise floor set by the synthesizer (about 11.5 min and 1.2 bpm given the per-device noise and the unidentifiable per-user offset). No model can do better on this data. Steps has some headroom left.
- Assessment at the time of this run: LightGBM and the Transformer are interchangeable on accuracy; LightGBM is marginally better calibrated and trains in minutes on CPU. The model selected afterwards, and served, is the Transformer; see "Model selection" below.

## 2. Specific conclusions

### Paired versus single-device users (transformer, test MAE)

| Cohort | Sleep (min) | Steps | Resting HR (bpm) |
|---|---|---|---|
| Apple Watch only | 11.5 | 1,349 | 1.06 |
| Fitbit only | 13.7 | 780 | 1.38 |
| Paired | **11.1** | **646** | **1.01** |

- Two devices beat one on every metric. The second reading lets the model cross-check.
- Fitbit is the noisier device for sleep; Apple Watch is the noisier device for steps, because its daily total is rebuilt from hourly intervals with wear gaps.
- Paired users also get the narrowest intervals.

### History length

- Error is flat from the first week to beyond 30 days of history for every model.
- Expected: a single device's per-user offset cannot be identified from one channel, however long it is observed. Rolling-history features capture the user's level, not the device's offset.

### What the models remove and what remains (structure checks)

- The constant device bias is fully removed by LightGBM and the transformer: mean removed matches the true mean bias (for example 20.1 of 23.6 min for Apple Watch sleep, 2.06 against 1.85 bpm for Fitbit heart rate).
- The residual that remains correlates 0.26 to 0.37 with the true bias: this is the per-user offset that one channel cannot reveal.
- Diffusion removes only part of the constant (4.5 of 13.0 min for Fitbit sleep), which is why it loses to the rules.

### Calibration differences

- LightGBM: coverage 0.89 to 0.90, intervals as narrow as the transformer's. Usable as stated.
- Transformer: coverage 0.92 to 0.93 with similar width. Slightly under-confident; harmless.
- Diffusion: coverage 0.98 to 0.99 with intervals four to five times wider. Hedging, not estimating.

### Generalisation and rules

- Validation and test numbers agree to the second decimal for every model: no overfitting to the training users.
- Every model gives no estimate on a day without any reading and an estimate on every day with one (100 % both ways).
- Small residual biases: the transformer under-estimates steps by about 190 per day and sleep by 3.5 min; LightGBM is closer to zero.

## Model selection (decided after this run)

The diffusion model is dropped on the evidence above. LightGBM and the Transformer are a tie on
accuracy and calibration; the Transformer is the family carried forward because its context window
is a testable design choice (see [pipeline-window-comparison.md](pipeline-window-comparison.md)),
it is the better of the two on steps, the only metric with headroom above the synthetic noise
floor, it extends to next-day prediction and windowed tool inputs unchanged, and it trains in
minutes on a GPU. LightGBM is kept as the calibrated baseline. The served model is the 14-day
Transformer ([../reconciliation.md](../reconciliation.md)).
The reasoning is written out in the root README ("Why three models, and why the Transformer").

## Limits

- Ground truth and device bias are the synthesizer's, drawn from literature priors. These results show that known structure is recovered with calibrated uncertainty on synthetic data; they are not a measurement of real Fitbit or Apple Watch accuracy.
- Diffusion ran with the CPU configuration (40 epochs, 64 channels, 50 samples per window). A larger GPU configuration was not tested.
- The smoke run exposed a data-preparation bug (Fitbit sleep nights dropped by a timestamp-precision mismatch), fixed in `src/model-training/training/data.py` with a regression test before the full runs.
