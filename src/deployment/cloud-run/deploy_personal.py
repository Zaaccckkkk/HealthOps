"""Deploy the separate single-owner pilot with application-token authentication.

Cloud Run invocation is public for this service only. Every data/tool/assistant route still
requires a revocable HealthOps token. This is not Google login or a public signup system.
"""

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from deploy import command as synthetic_command


def command(config):
    project = config.get("project", "")
    if (
        config.get("service") != "healthops-personal-api"
        or config.get("database_secret") != "healthops-personal-database-url"
        or config.get("service_account")
        != f"healthops-personal-run@{project}.iam.gserviceaccount.com"
    ):
        raise ValueError(
            "Personal deployment requires its dedicated service, secret and runtime identity"
        )
    result = synthetic_command(config)
    result = [
        "--allow-unauthenticated"
        if arg == "--no-allow-unauthenticated"
        else arg.replace(
            "HEALTHOPS_DATA_MODE=synthetic", "HEALTHOPS_DATA_MODE=personal"
        )
        for arg in result
    ]
    oauth = config.get("google_oauth")
    if oauth is not None:
        # Only resource references belong in config; values stay in Secret Manager.
        required = {"client_id", "client_secret_version", "connection_key_version"}
        if not isinstance(oauth, dict) or set(oauth) != required:
            raise ValueError(
                "Google OAuth requires only client ID and pinned secret versions"
            )
        if not re.fullmatch(
            r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", oauth["client_id"]
        ):
            raise ValueError("Expected a Google OAuth Web client ID")
        for key in ("client_secret_version", "connection_key_version"):
            if not re.fullmatch(r"[1-9][0-9]*", str(oauth[key])):
                raise ValueError("Pin a numeric Google OAuth secret version")
        base = urlsplit(config.get("url", ""))
        if (
            base.scheme != "https"
            or not base.hostname
            or not base.hostname.startswith("healthops-personal-api-")
            or not base.hostname.endswith(".run.app")
            or base.username
            or base.password
            or base.port is not None
            or base.path not in ("", "/")
            or base.query
            or base.fragment
        ):
            raise ValueError(
                "Google OAuth requires this personal service's HTTPS base URL"
            )
        redirect = f"https://{base.hostname}/v1/connections/google-health/callback"
        env = (
            f",HEALTHOPS_GOOGLE_OAUTH_CLIENT_ID={oauth['client_id']}"
            f",HEALTHOPS_GOOGLE_OAUTH_REDIRECT_URI={redirect}"
        )
        secrets = (
            ",HEALTHOPS_GOOGLE_OAUTH_CLIENT_SECRET=healthops-personal-google-client-secret:"
            + str(oauth["client_secret_version"])
            + ",HEALTHOPS_CONNECTION_KEY=healthops-personal-connection-key:"
            + str(oauth["connection_key_version"])
        )
        for index, arg in enumerate(result):
            if arg.startswith("--set-env-vars="):
                result[index] += env
            elif arg.startswith("--set-secrets="):
                result[index] += secrets
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cmd = command(json.loads(args.config.read_text()))
    if args.apply:
        subprocess.run(cmd, check=True)
    else:
        print(shlex.join(cmd))


if __name__ == "__main__":
    main()
