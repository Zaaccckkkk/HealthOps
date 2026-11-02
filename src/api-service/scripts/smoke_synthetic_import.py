"""Read-only HTTP checks for locally imported synthetic accounts; never print tokens/values."""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    url = urlparse(args.api_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Only a local HTTP API is supported.")
    report = json.loads((args.directory / "report.json").read_text())
    if report["status"] != "completed":
        raise ValueError("Import has not completed.")
    with httpx.Client(base_url=args.api_url, timeout=60, trust_env=False) as client:
        client.get("/health/ready").raise_for_status()
        for user in report["users"]:
            if not user["records"]:
                continue
            credentials = json.loads((args.directory / user["credentials_file"]).read_text())
            if credentials["user_id"] != user["api_user_id"]:
                raise ValueError("Credential identity mismatch.")
            if len(user["timezones"]) != 1:
                raise ValueError("This smoke check requires one source timezone per account.")
            headers = {"Authorization": f"Bearer {credentials['token']}"}
            me = client.get("/v1/me", headers=headers)
            me.raise_for_status()
            if me.json()["id"] != user["api_user_id"]:
                raise ValueError("API identity mismatch.")
            params = {
                "start_date": user["first_date"],
                "end_date": user["last_date"],
                "timezone": user["timezones"][0],
            }
            availability = client.get("/v1/tools/availability", headers=headers, params=params)
            availability.raise_for_status()
            actual = {
                f"{s['provider']}/{s['metric']}/{s['aggregation']}": s["record_count"]
                for s in availability.json()["series"]
            }
            if actual != user["series"]:
                raise ValueError("API series/counts do not match the import report.")
            metric = sorted(user["series"])[0].split("/")[1]
            end = date.fromisoformat(user["last_date"])
            summary = client.get(
                "/v1/tools/daily-summary",
                headers=headers,
                params=params
                | {"metric": metric, "start_date": (end - timedelta(days=6)).isoformat()},
            )
            summary.raise_for_status()
            print(f"PASS: {user['user_id']}: identity, {user['records']} records, daily summary.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # HTTP exceptions may contain URLs and other context; report only their type.
        raise SystemExit(f"Smoke check failed: {type(exc).__name__}.") from None
