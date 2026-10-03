"""Pure mapping and sync-report tests for the Google Health connector (no database)."""

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from fake_google_health import (
    API,
    AUTHORIZE_URL,
    REVOKE_URL,
    TOKEN_URL,
    FakeGoogle,
    hrv,
    resting,
    sleep,
    steps_interval,
)

from healthops.connectors.crypto import TokenCipher
from healthops.connectors.google_health import (
    GoogleHealthClient,
    GoogleOAuthConfig,
    map_hrv,
    map_resting_heart_rate,
    map_sleep,
    map_steps_interval,
    sync_window,
)
from healthops.repository import ConnectorIngestResult

NY = ZoneInfo("America/New_York")


def config():
    return GoogleOAuthConfig(
        client_id="client",
        client_secret="secret",
        redirect_uri="https://example.test/cb",
        authorize_url=AUTHORIZE_URL,
        token_url=TOKEN_URL,
        revoke_url=REVOKE_URL,
        api_base_url=API,
    )


def test_steps_interval_keeps_the_vendor_interval_and_platform_identity():
    record = map_steps_interval(
        steps_interval("2026-10-01T13:00:00Z", "2026-10-01T13:01:00Z", 64), NY
    )
    assert record.metric == "steps" and record.aggregation == "interval_total"
    assert record.value == Decimal(64) and record.unit == "count"
    assert (
        record.source_record_id
        == "steps:2026-10-01T13:00:00+00:00:2026-10-01T13:01:00+00:00:FITBIT"
    )
    assert record.source_device == "Synthetic Tracker"
    assert map_steps_interval({"steps": {"count": "1"}}, NY) is None  # no interval: unmappable


def test_resting_and_hrv_use_date_identities_and_separate_hrv_metric():
    r = map_resting_heart_rate(resting("2026-10-01", 58), NY)
    assert r.source_record_id == "daily-resting-heart-rate:2026-10-01:FITBIT"
    assert r.value == Decimal(58)
    h = map_hrv(hrv("2026-10-01", 42.5), NY)
    assert h.metric == "hrv_rmssd" and h.unit == "ms" and h.value == Decimal("42.500000")


def test_sleep_uses_minutes_asleep_and_interval_identity():
    s = map_sleep(sleep("2026-10-01T03:00:00Z", "2026-10-01T10:30:00Z", 410), NY)
    assert s.metric == "sleep_duration" and s.aggregation == "interval_total"
    assert s.value == Decimal(410 * 60) and s.unit == "s"
    assert s.source_record_id.startswith("sleep:2026-10-01T03:00:00+00:00:")


def test_sync_window_moves_watermarks_only_for_fully_fetched_types():
    fake = FakeGoogle()
    fake.fail_type = "sleep"  # second page of sleep fails
    client = GoogleHealthClient(config(), transport=fake.transport())
    ingested = []

    def ingest(records):
        ingested.extend(records)
        result = ConnectorIngestResult()
        result.inserted = len(records)
        return result

    cursors = {"sleep": "2026-09-20"}
    report = sync_window(
        client, "access", ingest, date(2026, 10, 1), date(2026, 10, 2), NY, cursors
    )
    by_type = {t.data_type: t for t in report.types}
    assert by_type["steps"].inserted == 2 and by_type["steps"].watermark == "2026-10-03"
    # Apple Health copies inside Google Health are skipped, not ingested as Fitbit data.
    assert by_type["steps"].skipped_other_platform == 1
    assert by_type["daily-resting-heart-rate"].skipped_other_platform == 1
    assert by_type["daily-resting-heart-rate"].inserted == 2
    assert by_type["sleep"].error.startswith("google 503") and by_type["sleep"].inserted == 0
    assert cursors["sleep"] == "2026-09-20"  # unchanged: partial window never counts as done
    assert all(r.provider == "google_health" for r in ingested)
    # Pages: sleep page size is 1 in the fake, so the first page was fetched before the failure.
    assert fake.requests.count("GET /v4/users/me/dataTypes/sleep/dataPoints") == 2


def test_authorization_url_requests_offline_access_and_carries_state():
    url = GoogleHealthClient(config()).authorization_url("state-123")
    assert (
        "access_type=offline" in url
        and "state=state-123" in url
        and "googlehealth.sleep.readonly" in url
    )


def test_token_cipher_round_trip_and_wrong_key_detection():
    key = TokenCipher.generate_key()
    token = TokenCipher(key).encrypt("refresh-secret")
    assert TokenCipher(key).decrypt(token) == "refresh-secret"
    other = TokenCipher(TokenCipher.generate_key())
    try:
        other.decrypt(token)
    except ValueError:
        pass
    else:
        raise AssertionError("a foreign key must not decrypt")
