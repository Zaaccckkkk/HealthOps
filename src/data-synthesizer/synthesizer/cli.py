"""data-synthesizer: hybrid synthetic cohort calibrated on the canonical real data.

Input  : DATA_ROOT/processed/canonical/<INPUT_DATA_VERSION>/ and the Synthea tables of the raw
         version that canonical version was built from; configs/*.yaml
Output : DATA_ROOT/synthetic/<OUTPUT_DATA_VERSION>/
           observations_healthkit.parquet       Apple Watch rows (canonical schema)
           observations_google_health.parquet   Fitbit rows (canonical schema)
           users.parquet                        cohort, Synthea profile, baselines
           latent_daily.parquet                 true daily state and context (ground truth)
           context_events.parquet               events with their source (llm or library)
           missingness_events.parquet           every missing-data decision (ground truth)
           device_bias_daily.parquet            systematic device error applied per day (v0.2+)
           calibration.json, context_library.json, llm_cache.jsonl (when an LLM was used)
         DATA_ROOT/manifests/<OUTPUT_DATA_VERSION>.yaml

Pipeline: calibrate -> Synthea profiles -> baselines -> context (LLM) -> latent trajectory
          -> device bias -> Apple Watch / Fitbit observation models with provider-specific
          missingness.
"""

import json
import logging
import os
from collections import Counter

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from synthesizer import (
    baseline,
    calibrate,
    clinical,
    context,
    device_bias,
    missingness,
    observe,
    trajectory,
)
from synthesizer.schema import ARROW_SCHEMA, to_table, validate
from synthesizer.versioning import (
    already_built,
    config_hashes,
    env_versions,
    input_reference,
    load_config,
    read_manifest,
    setup_logging,
    version_dir,
    write_manifest,
)

log = logging.getLogger("data-synthesizer")
CONFIGS = [
    "baseline.yaml",
    "trajectory.yaml",
    "apple_watch.yaml",
    "fitbit.yaml",
    "missingness.yaml",
    "context_generation.yaml",
]
WRITE_EVERY = 200  # users per parquet row group


def llm_client(cfg: dict, out) -> context.LLMClient | None:
    """The LLM client, or None (seed library only) when no model can be reached.

    Cached answers (llm.reuse_cache_from) are replayed even when the model is offline.
    """
    base_url = (os.environ.get("CONTEXT_LLM_BASE_URL") or "").strip()
    if base_url.lower() in ("", "off", "none"):
        log.warning("CONTEXT_LLM_BASE_URL unset/off: using the seed event library only")
        return None
    cache_file = out / "llm_cache.jsonl"
    reuse = cfg["llm"].get("reuse_cache_from")
    cache = context.load_cache(
        *([version_dir(reuse) / "llm_cache.jsonl"] if reuse else []), cache_file
    )
    client = context.LLMClient(base_url, cfg["llm"], cache, cache_file)
    reason = client.unavailable_reason()
    if reason and not cache:
        log.warning("LLM not available: %s; using the seed event library", reason)
        return None
    if reason:
        log.warning("LLM not available: %s; replaying %d cached answers", reason, len(cache))
    return client


def prompt_fields(user: pd.Series) -> dict:
    return {
        "age": int(user["age"]),
        "sex": user["sex"],
        "conditions": clinical.prompt_conditions(user["conditions"]),
        "medications": ", ".join(m.split(" ")[0] for m in user["medications"][:5]) or "none",
        "start_weekday": pd.Timestamp(user["start_date"]).day_name(),
        "exercise_days_per_week": round(float(user["exercise_probability"]) * 7, 1),
    }


def main() -> int:
    setup_logging()
    input_version, output_version = env_versions("data-synthesizer", "canonical", "synthetic")
    canonical_manifest = read_manifest(input_version)
    raw_version = canonical_manifest["input"]["version"]
    cfg = {name: load_config(name) for name in CONFIGS}
    canonical, out = version_dir(input_version), version_dir(output_version)
    if already_built(output_version, config_hashes(CONFIGS), None):
        return 0
    out.mkdir(parents=True, exist_ok=True)
    ctx_cfg = cfg["context_generation.yaml"]
    client = llm_client(ctx_cfg, out)
    parameters = {"context_llm_enabled": client is not None}
    seed = cfg["baseline.yaml"]["seed"]
    rng = np.random.default_rng(seed)

    log.info("calibrating on %s", input_version)
    calibration = {
        "fitbit": calibrate.fitbit(
            pd.read_parquet(canonical / "nethealth.parquet"), cfg["fitbit.yaml"]["calibration"]
        ),
        "apple_watch": calibrate.apple_watch(
            pd.read_parquet(canonical / "bidsleep.parquet"), cfg["apple_watch.yaml"]["calibration"]
        ),
    }
    (out / "calibration.json").write_text(json.dumps(calibration, indent=2))

    effects = cfg["baseline.yaml"]["clinical_effects"]
    profiles = clinical.load_profiles(
        version_dir(raw_version) / "synthea" / "csv", cfg["baseline.yaml"]["synthea"], effects
    )
    users = baseline.build_users(rng, profiles, calibration, cfg["baseline.yaml"])
    log.info("%d users from %d Synthea profiles", len(users), len(profiles))
    device_cfg = {"healthkit": cfg["apple_watch.yaml"], "google_health": cfg["fitbit.yaml"]}
    # Per-user device offsets come from a stream of their own (a three-element seed can never
    # equal a user's [seed, i] stream), so every other column stays identical to synthetic-v0.1.
    users = users.assign(
        **device_bias.user_offsets(np.random.default_rng([seed, 0, 2]), len(users), device_cfg)
    )

    library, library_source = context.build_library(client, ctx_cfg)
    (out / "context_library.json").write_text(json.dumps(library, indent=2))
    schedules: dict[str, list[dict]] = {}
    if client is not None:
        eligible = users[users["cohort"].isin(ctx_cfg["per_user"]["cohorts"])]
        chosen = eligible.sample(
            min(len(eligible), ctx_cfg["per_user"]["max_users"]), random_state=seed
        )
        for i, (_, user) in enumerate(chosen.iterrows(), 1):
            try:
                schedules[user["user_id"]] = context.per_user_schedule(
                    client, ctx_cfg, prompt_fields(user), int(user["n_days"]), library
                )
            except context.ContextError as exc:
                log.warning("%s: %s; falling back to the library", user["user_id"], exc)
            if i % 10 == 0:
                log.info("per-user schedules: %d/%d", i, len(chosen))

    params = trajectory.parameters(cfg["trajectory.yaml"], calibration)
    plans_cfg = {
        p: missingness.resolve(cfg["missingness.yaml"], p, calibration) for p in device_cfg
    }
    writers = {
        p: pq.ParquetWriter(out / f"observations_{p}.parquet", ARROW_SCHEMA) for p in device_cfg
    }
    buffers = {p: [] for p in device_cfg}
    latent_parts, event_rows, missing_rows, context_sources = [], [], [], []
    bias_parts = []
    stats = {
        "rows": Counter(),
        "dropped": Counter(),
        "missingness": Counter(),
        "context": Counter(),
    }

    def flush(provider: str) -> None:
        if buffers[provider]:
            rows, dropped = validate(pd.concat(buffers[provider], ignore_index=True))
            writers[provider].write_table(to_table(rows))
            stats["rows"].update(
                f"{provider}/{m}/{a}" for m, a in zip(rows.metric, rows.aggregation)
            )
            stats["dropped"].update(dropped)
            buffers[provider] = []

    for i, user in users.iterrows():
        user_rng = np.random.default_rng([seed, i])
        n_days = int(user["n_days"])
        events = schedules.get(user["user_id"]) or context.sample_events(
            user_rng, n_days, library, ctx_cfg
        )
        source = "llm_per_user" if user["user_id"] in schedules else library_source
        stats["context"][source] += 1
        context_sources.append(source)
        for event in events:
            details = {k: v for k, v in event.items() if k != "context"}
            event_rows.append({"user_id": user["user_id"], **details, **event["context"]})
        days = context.daily_context(n_days, events, ctx_cfg["default_context"])
        latent = trajectory.simulate(user, days, params, user_rng)
        latent_parts.append(latent)
        for provider in user["providers"].split(","):
            plan = missingness.plan(user_rng, n_days, plans_cfg[provider])
            for event in plan["events"]:
                missing_rows.append(
                    {
                        "user_id": user["user_id"],
                        "provider": provider,
                        "date": latent["date"].iloc[event["day_index"]],
                        **event,
                    }
                )
                stats["missingness"][f"{provider}/{event['mechanism']}"] += 1
            bias = device_bias.daily(user, latent, device_cfg[provider], provider)
            bias_parts.append(bias)
            model = observe.MODELS[provider]
            buffers[provider].append(
                model(
                    user,
                    latent,
                    plan,
                    device_cfg[provider],
                    calibration,
                    output_version,
                    user_rng,
                    bias=device_bias.arrays(bias),
                )
            )
        if (i + 1) % WRITE_EVERY == 0:
            for provider in device_cfg:
                flush(provider)
            log.info("generated %d/%d users", i + 1, len(users))
    for provider in device_cfg:
        flush(provider)
        writers[provider].close()

    users.assign(context_source=context_sources).to_parquet(out / "users.parquet", index=False)
    pd.concat(latent_parts, ignore_index=True).to_parquet(out / "latent_daily.parquet", index=False)
    pd.DataFrame(event_rows).to_parquet(out / "context_events.parquet", index=False)
    pd.DataFrame(missing_rows).to_parquet(out / "missingness_events.parquet", index=False)
    bias_daily = pd.concat(bias_parts, ignore_index=True)
    bias_daily.to_parquet(out / "device_bias_daily.parquet", index=False)
    bias_summary = {
        f"{provider}/{metric}": round(float(value), 4)
        for (provider, metric), value in bias_daily.groupby(["provider", "metric"])["bias_total"]
        .mean()
        .items()
    }

    write_manifest(
        output_version,
        "data-synthesizer",
        "Hybrid synthetic cohort: calibrated baselines, longitudinal trajectories, LLM context, "
        "Synthea profiles, Apple Watch and Fitbit observation models with systematic device "
        "bias (priors), realistic missingness.",
        input=input_reference(input_version),
        raw_input=input_reference(raw_version),
        configs=config_hashes(CONFIGS),
        parameters=parameters,
        stats={
            "users": len(users),
            "users_by_cohort": users["cohort"].value_counts().to_dict(),
            "synthea_profiles_available": len(profiles),
            "context_library": {"source": library_source, "templates": len(library)},
            "context_source_by_user": dict(stats["context"]),
            "rows_by_metric": dict(sorted(stats["rows"].items())),
            "dropped_by_validation": dict(stats["dropped"]),
            "missingness_events": dict(sorted(stats["missingness"].items())),
            "mean_device_bias": bias_summary,
            "calibration_users": {
                "fitbit": calibration["fitbit"]["steps_log"]["users"],
                "apple_watch": calibration["apple_watch"]["resting_hr_proxy"]["users"],
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
