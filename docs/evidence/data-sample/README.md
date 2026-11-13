# Data pipeline sample: one synthetic user, input -> output

From `synthetic-v0.1` (see `data/manifests/synthetic-v0.1.yaml` for the full version, its file hashes and
its inputs). One paired user (Apple Watch and Fitbit), `synthetic-00012`, over 7 consecutive days
(2026-07-05 to 2026-07-11, America/New_York), chosen because both devices have missing data in this
window and the user has a personal schedule written by the LLM.
Everything here is synthetic.

| File | Role | Rows |
|---|---|---|
| `user.csv` | Input: cohort, Synthea profile (age, sex, conditions, medications) and personal baselines | 1 |
| `latent_daily.csv` | Input: the true daily state (resting HR, HRV, steps, sleep, sleep debt, training load) and the day's context and event | 7 |
| `observations_healthkit.csv` | Output: what the Apple Watch recorded, in the canonical schema (exercise_duration 6, heart_rate 82, hrv_sdnn 8, resting_heart_rate 7, sleep_duration 8, steps 123; heart-rate samples for the first day only) | 234 |
| `observations_google_health.csv` | Output: what the Fitbit recorded (exercise_duration 7, heart_rate 7, hrv_rmssd 3, resting_heart_rate 6, sleep_duration 4, steps 7) | 34 |
| `missingness_events.csv` | Output: every missing-data decision in the window (Apple Watch: battery_depletion, metric_dropout, overnight_not_worn; Fitbit: metric_dropout, overnight_not_worn) | 8 |

Read across the files: a day in `latent_daily.csv` is observed by both device models; a day or
metric listed in `missingness_events.csv` is absent from that device's observations.
