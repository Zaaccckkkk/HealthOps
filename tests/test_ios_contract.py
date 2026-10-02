"""Cross-language contract: the Swift adapter's actual encoding must satisfy the Python schema.

The committed fixture is validated everywhere. On a host with the Swift toolchain, the fixture
is regenerated from the Swift package and compared byte-for-byte so it cannot go stale.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from healthops.repository import fingerprint
from healthops.schemas import Metric, ObservationBatch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/ios-upload.synthetic.json"
PACKAGE = ROOT / "ios/HealthOpsUploader"


def test_committed_fixture_is_a_valid_batch():
    batch = ObservationBatch.model_validate_json(FIXTURE.read_text())
    metrics = {o.metric for o in batch.observations}
    assert Metric.HRV_RMSSD not in metrics  # HealthKit provides SDNN only
    assert {o.provider for o in batch.observations} == {"healthkit"}
    sleep = next(o for o in batch.observations if o.metric == Metric.SLEEP_DURATION)
    assert sleep.aggregation == "interval_total"
    assert sleep.value == (sleep.ended_at - sleep.started_at).total_seconds()
    exercise = next(o for o in batch.observations if o.metric == Metric.EXERCISE_DURATION)
    assert (exercise.unit, exercise.value) == ("s", 300)
    # Samples without time-zone metadata use the fixed UTC fallback, so replays stay identical.
    zones = {o.source_timezone for o in batch.observations}
    assert zones == {"UTC", "America/New_York"}
    assert len({fingerprint(o) for o in batch.observations}) == len(batch.observations)


@pytest.mark.skipif(
    shutil.which("swift") is None or not PACKAGE.exists(),
    reason="Swift toolchain not available here; the committed fixture is still validated.",
)
def test_swift_output_matches_committed_fixture():
    result = subprocess.run(
        ["swift", "run", "--package-path", str(PACKAGE), "healthops-fixture"],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert json.loads(result.stdout) == json.loads(FIXTURE.read_text())
    assert result.stdout.strip() == FIXTURE.read_text().strip(), "regenerate the fixture"
