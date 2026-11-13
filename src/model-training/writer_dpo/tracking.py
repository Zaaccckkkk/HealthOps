"""Private, reproducible NeMo tracking configuration; credentials never enter artifacts."""

import base64
import hashlib
import importlib.metadata
import os
import platform
import re
import subprocess
from pathlib import Path


def logger_config(output, environ=None):
    env = os.environ if environ is None else environ
    mode = env.get("WANDB_MODE") or "offline"
    if mode not in {"offline", "online", "disabled"}:
        raise ValueError("WANDB_MODE must be offline, online or disabled")
    project = env.get("WANDB_PROJECT") or "healthops-writer-dpo"
    entity = env.get("WANDB_ENTITY") or None
    if mode == "online" and (not entity or not env.get("WANDB_API_KEY")):
        raise ValueError("Online tracking requires an explicit personal entity and injected key")
    # Cloud containers reuse the same local path; the immutable output URI separates jobs.
    identity = env.get("HEALTHOPS_RUN_IDENTITY") or str(Path(output).resolve())
    run_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
    wandb = {"project": project, "name": Path(output).name, "id": run_id, "resume": "allow"}
    if entity:
        wandb["entity"] = entity
    return {
        "log_dir": str(Path(output) / "logs"),
        "wandb_enabled": mode != "disabled",
        "wandb": wandb,
        "tensorboard_enabled": True,
        "monitor_gpus": True,
        "num_val_samples_to_print": 0,
    }, {"mode": mode, "project": project, "entity": entity, "run_id": run_id}


def inject_wandb_secret(resource, environ=None, session=None):
    """Read a pinned Secret Manager version into process memory, never YAML/job args."""
    if not re.fullmatch(r"projects/[\w-]+/secrets/[\w-]+/versions/[1-9]\d*", resource):
        raise ValueError("Use a pinned numeric Secret Manager version")
    if session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(credentials)
    response = session.get(f"https://secretmanager.googleapis.com/v1/{resource}:access", timeout=30)
    if response.status_code != 200:
        raise RuntimeError("W&B secret access failed; check the worker service account")
    key = base64.b64decode(response.json()["payload"]["data"], validate=True).decode().strip()
    if not key or "\n" in key:
        raise ValueError("Invalid W&B credential payload")
    (os.environ if environ is None else environ)["WANDB_API_KEY"] = key


def runtime_record():
    versions = {}
    for package in ("torch", "wandb", "ray", "transformers", "nemo-rl", "megatron-core"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "working_directory_revision": revision,
        "package_versions": versions,
    }
