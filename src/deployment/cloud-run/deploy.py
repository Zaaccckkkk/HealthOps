"""Render or deploy the existing API image to an IAM-protected Cloud Run service."""

import argparse
import json
import shlex
import subprocess
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse


def command(config):
    required = (
        "project",
        "region",
        "service",
        "image",
        "service_account",
        "network",
        "subnet",
        "database_secret",
        "database_secret_version",
        "model_url",
    )
    if any(not config.get(key) for key in required):
        raise ValueError("Missing deployment configuration")
    if config["database_secret_version"] == "latest":
        raise ValueError("Pin the database secret version for reproducible revisions")
    url = urlparse(config["model_url"])
    if (
        url.scheme != "http"
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or url.port != 11434
        or ip_address(url.hostname) not in ip_network("10.0.0.0/8")
    ):
        raise ValueError("This deployment expects a private VPC model endpoint")
    env = {
        "HEALTHOPS_ENVIRONMENT": "cloud-dev",
        "HEALTHOPS_ASSISTANT_MODEL_BASE_URL": config["model_url"],
        "HEALTHOPS_ASSISTANT_MODEL_BACKEND": "ollama",
        "HEALTHOPS_ASSISTANT_MODEL_NAME": "qwen3:8b",
        "HEALTHOPS_ASSISTANT_THINKING": "false",
        "HEALTHOPS_ASSISTANT_MAX_CONCURRENCY": "1",
        "HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE": "false",
    }
    return [
        "gcloud",
        "run",
        "deploy",
        config["service"],
        f"--project={config['project']}",
        f"--region={config['region']}",
        f"--image={config['image']}",
        f"--service-account={config['service_account']}",
        "--port=8000",
        "--cpu=1",
        "--memory=1Gi",
        "--concurrency=4",
        "--min=0",
        "--max=1",
        "--timeout=180",
        "--no-allow-unauthenticated",
        f"--network={config['network']}",
        f"--subnet={config['subnet']}",
        "--vpc-egress=private-ranges-only",
        "--network-tags=healthops-cloud-run",
        "--set-env-vars=" + ",".join(f"{k}={v}" for k, v in env.items()),
        "--set-secrets=HEALTHOPS_DATABASE_URL="
        + config["database_secret"]
        + ":"
        + str(config["database_secret_version"]),
        "--quiet",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cmd = command(json.loads(args.config.read_text()))
    if args.apply:
        subprocess.run(cmd, check=True)
    else:
        print(shlex.join(cmd))


if __name__ == "__main__":
    main()
