# Device bias in the synthetic cohort (synthetic-v0.2)

## Why

In `synthetic-v0.1` both device models observe the same true day with small, independent,
zero-mean noise (`configs/apple_watch.yaml`, `configs/fitbit.yaml`, `metrics.*.noise_*`). Two
devices on the same person therefore never disagree by more than that noise, and nothing can be
learned about which device to trust. Real consumer wearables are biased in known directions.
From `synthetic-v0.2` on, the observation models report

    observation = latent value + device bias + noise

with the bias computed by `src/data-synthesizer/synthesizer/device_bias.py` from the `bias:`
block of each device config. Every applied value is written to `device_bias_daily.parquet`
(`bias_constant`, `bias_user`, `bias_context`, `bias_total` per user, day, provider and metric),
so a model trained on the synthetic data can be checked against the error it was meant to
recover. `latent_daily.parquet` is unchanged and stays device-free.

## Structure

| Part | Meaning | Config keys |
|---|---|---|
| constant | the same for every user of the device | `mean_minutes`, `log_mean`, `mean_bpm` |
| user | a stable per-user offset drawn once per provider (how the device is worn); a column of `users.parquet` | `user_sd_minutes`, `user_log_sd`, `user_sd_bpm` |
| context | day-dependent: illness or travel nights (fragmented sleep), hard-workout days, days below `low_activity_steps` | `fragmented_night_extra_minutes`, `hard_workout_log_extra`, `low_activity_log_extra` |

The per-user offsets come from a random stream of their own, so every other column of
`users.parquet`, the latent trajectories, the context events and the missingness decisions are
identical to a run without the bias block (`tests/test_device_bias.py`). Removing the block, or
setting every value to 0, reproduces `synthetic-v0.1`.

## Values and their sources

All values are **priors** (`source: prior`): no dataset we hold records the same person on both
devices together with a reference measurement, so nothing here is calibrated. Each value sits
inside the range reported by the studies below and must be reviewed before any result that
depends on it is reported as a finding (see `AGENTS.md`).

### Sleep duration (minutes of reported asleep time, device minus truth)

| Device | constant | user SD | fragmented-night extra | Evidence |
|---|---|---|---|---|
| Apple Watch | +20 | 15 | +15 | Apple Watch Series 8 vs PSG: TST +19.6 min, WASO −21.2 min, specificity 52% (Schyvens et al. 2025) |
| Fitbit | +10 | 15 | +15 | Fitbit Sense / Charge 5 vs PSG: TST +6 to +11 min, WASO −12 to −17 min (Schyvens et al. 2025); non-staging Fitbit models +7 to +67 min TST, −6 to −44 min WASO (Haghayegh et al. 2019); −9 to −11 min in another sample (Sensors 2024) |

Direction is consistent across studies (total sleep over, wake under); magnitude is not, which
is why the per-user SD is wide. The context term follows the proportional bias reported for
WASO: devices over-score sleep most on the nights with the most wake (Sensors 2024:
bias = 42 − 0.62 × true WASO; Chinoy et al. 2021: performance drops on disrupted nights).
Pooled across all consumer wrist devices: TST 16.9 min, WASO 13.3 min (Lee et al. 2025).

### Steps (log scale, device minus truth)

| Device | constant | user SD | hard-workout extra | low-activity extra | Evidence |
|---|---|---|---|---|---|
| Apple Watch | −0.02 | 0.04 | 0 | −0.05 | most reliable of 12 devices at slow and altered gait (Rowe et al. 2025); wrist counters undercount when arm swing is restricted |
| Fitbit | +0.06 | 0.08 | +0.05 | −0.08 | overestimates in free living, underestimates in controlled walking (Feehan et al. 2018); +7% in younger adults, +36% to +39% in older adults vs hip ActiGraph (free-living Fitbit Charge 2 studies) |

### Resting heart rate (bpm, device minus truth)

| Device | constant | user SD | Evidence |
|---|---|---|---|
| Apple Watch | −1 | 1.0 | 24 h ambulatory ECG, one participant: Apple Watch 3 −1.8 bpm (Nelson and Allen 2019) |
| Fitbit | −2 | 1.5 | same study: Fitbit Charge 2 −3.5 bpm; vendors also define resting heart rate differently |

This is the weakest prior (single-subject evidence); set both blocks to 0 to switch it off.

## Modelling precedents

- Constant bias per run plus Gaussian noise per sample is the error model of physics-based
  sensor simulation (WIMUSim, 2025).
- A per-subject random effect for wearable measurement error is the structure of linear
  mixed-model error correction for device-based exposures (Chen et al. 2024).

## References

- Schyvens AM et al. A performance validation of six commercial wrist-worn wearable sleep-tracking devices for sleep stage scoring compared to polysomnography. SLEEP Advances 2025;6(2):zpaf021.
- Haghayegh S et al. Accuracy of wristband Fitbit models in assessing sleep: systematic review and meta-analysis. J Med Internet Res 2019;21(11):e16273.
- Lee YJ et al. Performance of consumer wrist-worn sleep tracking devices compared to polysomnography: a meta-analysis. J Clin Sleep Med 2025;21(3):573-582.
- Chinoy ED et al. Performance of seven consumer sleep-tracking devices compared with polysomnography. Sleep 2021;44(5):zsaa291.
- Evaluating accuracy in five commercial sleep-tracking devices compared to research-grade actigraphy and polysomnography. Sensors 2024;24(2):635.
- Feehan LM et al. Accuracy of Fitbit devices: systematic review and narrative syntheses of quantitative data. JMIR mHealth uHealth 2018;6(8):e10527.
- Rowe et al. Accurate measurement of simulated slow and altered walking activity: Apple Watch best in class wearable devices. PLoS One 2025.
- Nelson BW, Allen NB. Accuracy of consumer wearable heart rate measurement during an ecologically valid 24-hour period. JMIR mHealth uHealth 2019;7(3):e10828.
- WIMUSim: physically plausible data augmentations for wearable IMU-based human activity recognition using physics simulation. Frontiers in Computer Science 2025 (arXiv 2508.13284).
- Chen R et al. A linear mixed model approach for measurement error adjustment: applications to sedentary behavior assessment from wearable devices. arXiv 2403.01000 (2024).
