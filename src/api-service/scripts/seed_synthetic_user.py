"""Load the synthetic evaluation history into a user through the public ingestion API.

Used to give the synthetic phone-tester account something to ask about on a deployment where
no HealthKit data is uploaded. Reads a credential file written by `healthops create-user`
(never printed), posts `eval/synthetic_history.observations()` in batches of at most 500,
and reports inserted/duplicate counts. Re-running is replay-safe: the server answers with
duplicates. Synthetic data only; never point this at real health records.

    uv run python scripts/seed_synthetic_user.py https://host tester.json [--healthkit-only]

`--healthkit-only` drops synthetic `google_health` records for a HealthKit-only demo.
The iOS app can separately display Fitbit records synced by the server-side Google Health
connector; these records do not come from the phone's HealthKit store.
"""

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from synthetic_history import observations  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    base, credential = args[0].rstrip("/"), Path(args[1])
    token = json.loads(credential.read_text())["token"]
    records = observations()
    if "--healthkit-only" in sys.argv:
        records = [r for r in records if r["provider"] == "healthkit"]
    inserted = duplicates = 0
    for start in range(0, len(records), 500):
        body = json.dumps({"observations": records[start : start + 500]}).encode()
        request = urllib.request.Request(
            base + "/v1/observations/batch",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
        inserted += result.get("inserted", 0)
        duplicates += result.get("duplicates", 0)
    print(f"records {len(records)}: inserted {inserted}, duplicates {duplicates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
