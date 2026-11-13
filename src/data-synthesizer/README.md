# Data Synthesizer

Generates the hybrid synthetic cohort. The LLM never writes measurements: real datasets give
the distributions, a statistical generator writes the numbers, the LLM adds life-event context
and Synthea adds clinical background.

| | |
|---|---|
| Input | `data/processed/canonical/<INPUT_DATA_VERSION>/`, the Synthea tables of the raw version it came from, `configs/*.yaml` |
| Output | `data/synthetic/<OUTPUT_DATA_VERSION>/` (see below) and the manifest |
| Environment | `INPUT_DATA_VERSION` (`canonical-*`), `OUTPUT_DATA_VERSION` (`synthetic-*`), `CONTEXT_LLM_BASE_URL` (default `http://host.docker.internal:11434`; `off` disables), `DATA_ROOT`, `CONFIG_DIR`, `GIT_COMMIT` |

## Steps

| Step | Module | Config | What happens |
|---|---|---|---|
| Calibrate | `calibrate.py` | `fitbit.yaml`, `apple_watch.yaml` (`calibration`) | From NetHealth: steps, sleep, bedtime, daily mean HR, exercise frequency (between-user spread, within-user day-to-day SD, lag-1 autocorrelation, day-of-week pattern) and missingness (single missing days, multi-day gaps, missing nights). From BIDSleep: resting HR proxy (10th percentile of overnight HR), HR noise, HR coverage gaps, sleep per night. Saved as `calibration.json`. |
| A. Baselines | `baseline.py`, `clinical.py` | `baseline.yaml` | Each user gets stable personal baselines drawn from the calibrated population distributions, shifted by their Synthea profile (age, obesity, hypertension, diabetes, beta blockers). HRV uses a prior. |
| B. Trajectory | `trajectory.py` | `trajectory.yaml` | Day by day: baseline + AR(1) deviation + sleep debt + training load + context effects. Consecutive short nights raise resting HR and lower HRV gradually. |
| C. Context | `context.py` | `context_generation.yaml` | An LLM writes an event library once, and personal event schedules for a subset of users (default: paired users, up to 500). Events are structured (`stress`, `workout`, `travel`, `illness`, `subjective_fatigue`) and validated against a schema. Before any call the endpoint is probed (`/api/tags`, model pulled?); if Ollama is not running, the model is missing or the variable is `off`, the run logs why and uses the seed library in the config, and the manifest records `context_llm_enabled: false`. |
| D. Synthea | `clinical.py` | `baseline.yaml` | Active conditions and medications at the reference date become flags and prompt context. |
| E. Devices | `observe.py` | `apple_watch.yaml`, `fitbit.yaml` | The same latent day is observed separately: Apple Watch as HealthKit samples (HR every 10/15 min, daily RHR, nightly SDNN) and interval totals (hourly steps, exercise, asleep segments); Fitbit as daily totals and summaries (steps, mean HR, RHR, RMSSD, active minutes) and one sleep log per night. Since synthetic-v0.2 each model reports `latent + device bias + noise`. |
| Device bias | `device_bias.py` | `apple_watch.yaml`, `fitbit.yaml` (`bias`) | Systematic device error from literature priors: a device constant, a per-user offset drawn once from its own random stream, and a context term (illness or travel nights, hard workouts, low-activity days) for sleep, steps and resting HR. Every applied value is saved; without a `bias` block the output equals synthetic-v0.1 ([docs/device-bias.md](../../docs/device-bias.md)). |
| Missingness | `missingness.py` | `missingness.yaml` | Per provider: not worn, multi-day gaps, sync failure, battery depletion, nights not worn, metric dropout, HR sample dropout. Fitbit rates are calibrated on NetHealth; Apple Watch rates are priors except HR sample dropout. |

`calibrated` in a config means the value is estimated from canonical real data; numbers are
priors chosen by hand and must be reviewed before they are reported as findings.

## Outputs

| File | Contents |
|---|---|
| `observations_healthkit.parquet`, `observations_google_health.parquet` | Canonical rows (same schema as real data, `provenance_dataset = synthetic`) |
| `users.parquet` | Cohort (`apple_watch_only`, `fitbit_only`, `paired`), Synthea profile, baselines, context source |
| `latent_daily.parquet` | True daily state and context per user (ground truth for evaluation) |
| `context_events.parquet` | Every event with its source (`llm_per_user`, `library`) |
| `missingness_events.parquet` | Every missing-data decision (ground truth for "not enough data" answers) |
| `device_bias_daily.parquet` | The device bias applied to every user, day, provider and metric (`bias_constant`, `bias_user`, `bias_context`, `bias_total`); ground truth for reconciliation models |
| `calibration.json`, `context_library.json`, `llm_cache.jsonl` | Fitted statistics, the event library, raw LLM answers |

Reproducibility: one seed (`baseline.yaml`), a separate random stream per user, and cached LLM
answers (`llm.reuse_cache_from` reuses an earlier version's cache without calling the model;
it is set to `synthetic-v0.1`, so a rebuild under a new version name replays the 500 personal
schedules and the event library from `data/synthetic/synthetic-v0.1/llm_cache.jsonl` and makes
no LLM call as long as the users table is unchanged).
The LLM client mirrors the API assistant's backends (Ollama native `/api/chat` with
`think: false`, or an OpenAI-compatible endpoint); a host Ollama is reachable from the container
at `http://host.docker.internal:11434`.

Observed on `synthetic-v0.1` (2026-10-19, `qwen3:4b` through Ollama): all 500 personal schedules
passed validation; the event library reached 38 templates (8 seed + 30 generated) of the 60
requested: a generated template is kept only under a name not yet in the library, and
`build_library` stops after twice the needed number of calls. After the LLM step, generating
all 5000 users took about 2.5 minutes. Counts per metric and missingness mechanism are in
`data/manifests/synthetic-v0.1.yaml`.

```bash
docker compose --profile data run --rm data-synthesizer      # from the repository root
uv run pytest -q                                              # in this folder (offline smoke test)
```
