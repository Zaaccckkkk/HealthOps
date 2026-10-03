import pytest
from pydantic import ValidationError

from healthops.config import Settings
from healthops.repository import fingerprint
from healthops.schemas import ObservationBatch, ObservationIn


def test_normalizes_equivalent_time_and_number(record):
    first = ObservationIn.model_validate(record)
    record.update(
        value="72.000000", started_at="2026-10-01T12:00:00Z", ended_at="2026-10-01T12:00:00Z"
    )
    assert fingerprint(first) == fingerprint(ObservationIn.model_validate(record))


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "ms"},
        {"value": -1},
        {"value": "NaN"},
        {"value": "Infinity"},
        {"value": True},
        {"value": 0},
        {"value": "1.1234567"},
        {"source_record_id": "   "},
        {"started_at": "2026-10-01T08:00:00"},
        {"ended_at": "2026-09-01T08:00:00Z"},
        {"source_timezone": "not-a-zone"},
        {"metric": "hrv"},
        {"provider": "unknown"},
        {"user_id": "spoofed"},
        {"metric": "steps", "unit": "count", "value": 2.5},
    ],
)
def test_invalid_records(record, changes):
    with pytest.raises(ValidationError):
        ObservationIn.model_validate(record | changes)


def test_empty_or_oversize_batch(record):
    for records in ([], [record] * 501):
        with pytest.raises(ValidationError):
            ObservationBatch(observations=records)


def test_sdnn_rmssd_are_distinct(record):
    sdnn = ObservationIn.model_validate(record | {"metric": "hrv_sdnn", "unit": "ms"})
    rmssd = ObservationIn.model_validate(record | {"metric": "hrv_rmssd", "unit": "ms"})
    assert fingerprint(sdnn) != fingerprint(rmssd)


def test_cloud_dev_is_a_supported_environment():
    Settings(environment="cloud-dev").require_local()


def test_fail_closed_for_nonlocal_environment():
    with pytest.raises(ValueError, match="local and cloud-dev"):
        Settings(environment="production").require_local()
