"""Exercise the LOCAL HTTP API with synthetic records; never prints the token."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, default=Path(".local/demo-credentials.json"))
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This smoke script is restricted to loopback servers.")
    credentials = json.loads(args.credentials.read_text())
    example = Path(__file__).resolve().parents[1] / "examples/observations.synthetic.json"
    payload = json.loads(example.read_text())
    # A unique run makes first-write/retry behavior reproducible on a populated demo database.
    suffix = str(uuid4())
    for record in payload["observations"]:
        record["source_record_id"] += f"-{suffix}"
    with httpx.Client(
        base_url=args.url, headers={"Authorization": f"Bearer {credentials['token']}"}, timeout=10
    ) as client:
        first = client.post("/v1/observations/batch", json=payload)
        first.raise_for_status()
        assert first.json() == {"received": 2, "inserted": 2, "duplicates": 0}
        again = client.post("/v1/observations/batch", json=payload)
        again.raise_for_status()
        assert again.json() == {"received": 2, "inserted": 0, "duplicates": 2}
        expected = {r["source_record_id"] for r in payload["observations"]}
        found = set()
        offset = 0
        while True:
            history = client.get(
                "/v1/observations", params={"metric": "heart_rate", "limit": 200, "offset": offset}
            )
            history.raise_for_status()
            page = history.json()
            found.update(r["source_record_id"] for r in page["items"])
            if expected <= found or page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert expected <= found
        # Deterministic tools: the token decides whose history is summarized.
        availability = client.get(
            "/v1/tools/availability",
            params={
                "timezone": "America/New_York",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        availability.raise_for_status()
        series = {(s["provider"], s["metric"]) for s in availability.json()["series"]}
        assert {("healthkit", "heart_rate"), ("google_health", "heart_rate")} <= series
        summary = client.get(
            "/v1/tools/daily-summary",
            params={
                "metric": "heart_rate",
                "timezone": "America/New_York",
                "start_date": "2026-10-01",
                "end_date": "2026-10-01",
            },
        )
        summary.raise_for_status()
        providers = {p["provider"]: p for p in summary.json()["providers"]}
        assert providers["healthkit"]["days"][0]["sample_count"] >= 1
        assert providers["healthkit"]["days"][0]["sample_mean"] is not None
        comparison = client.get(
            "/v1/tools/period-comparison",
            params={
                "metric": "heart_rate",
                "timezone": "America/New_York",
                "baseline_start": "2026-09-24",
                "baseline_end": "2026-09-30",
                "current_start": "2026-10-01",
                "current_end": "2026-10-07",
            },
        )
        comparison.raise_for_status()
        compared = {p["provider"]: p for p in comparison.json()["providers"]}
        assert compared["healthkit"]["current"]["days_with_data"] >= 1
        assert compared["healthkit"]["baseline"]["value"] is None  # no synthetic baseline data
        assert compared["healthkit"]["difference"] is None
        print("PASS: inserted 2 synthetic observations; replay skipped 2; history query succeeded.")
        print("PASS: availability listed both synthetic providers; daily summary returned a mean.")
        print("PASS: period comparison returned a null difference for the empty baseline window.")
        print("Synthetic records remain in the local demo database for inspection.")


if __name__ == "__main__":
    main()
