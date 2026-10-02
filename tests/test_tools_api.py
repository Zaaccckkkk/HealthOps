"""Tool endpoints against real PostgreSQL: identity injection, SQL day counting, limits."""

import pytest

from healthops.tools import queries

pytestmark = pytest.mark.integration

NY = "America/New_York"
YEAR = {"timezone": NY, "start_date": "2026-01-01", "end_date": "2026-12-31"}


def upload(client, headers, *records):
    response = client.post(
        "/v1/observations/batch", headers=headers, json={"observations": records}
    )
    assert response.status_code == 200, response.text
    return response


def sample(record, source_record_id, started, metric="heart_rate", unit="bpm", value=70):
    return record | {
        "source_record_id": source_record_id,
        "started_at": started,
        "ended_at": started,
        "metric": metric,
        "unit": unit,
        "value": value,
    }


def test_tools_use_authenticated_identity_only(client, credentials, record):
    alice, bob = credentials
    upload(client, alice, record)
    assert client.get("/v1/tools/availability", params=YEAR).status_code == 401
    bob_id = client.get("/v1/me", headers=bob).json()["id"]
    # An extra user_id query parameter is ignored; the token decides whose data is read.
    mine = client.get(
        "/v1/tools/availability", headers=alice, params=YEAR | {"user_id": bob_id}
    ).json()
    assert [s["record_count"] for s in mine["series"]] == [1]
    assert client.get("/v1/tools/availability", headers=bob, params=YEAR).json()["series"] == []
    summary = client.get(
        "/v1/tools/daily-summary",
        headers=bob,
        params={
            "metric": "heart_rate",
            "timezone": NY,
            "start_date": "2026-10-01",
            "end_date": "2026-10-01",
        },
    ).json()
    assert summary["providers"] == [] and summary["warnings"] == ["no_records_in_window"]


def test_availability_counts_local_days_in_the_database(client, credentials, record):
    alice = credentials[0]
    upload(
        client,
        alice,
        sample(record, "a", "2026-10-01T03:00:00Z"),  # 23:00 Sep 30 New York
        sample(record, "b", "2026-10-01T12:00:00Z"),
        sample(record, "c", "2026-11-01T05:30:00Z"),  # 01:30 EDT, repeated hour
        sample(record, "d", "2026-11-01T06:30:00Z"),  # 01:30 EST, same local day
        sample(record, "e", "2026-10-01T12:00:00Z", metric="hrv_sdnn", unit="ms"),
        sample(record, "f", "2026-10-01T12:00:00Z", metric="hrv_rmssd", unit="ms"),
    )
    by_metric = {
        s["metric"]: s
        for s in client.get("/v1/tools/availability", headers=alice, params=YEAR).json()["series"]
    }
    assert set(by_metric) == {"heart_rate", "hrv_sdnn", "hrv_rmssd"}
    heart = by_metric["heart_rate"]
    assert (heart["record_count"], heart["local_days_with_records"]) == (4, 3)
    assert heart["first_started_at"].startswith("2026-10-01T03:00:00")
    utc = client.get(
        "/v1/tools/availability", headers=alice, params=YEAR | {"timezone": "UTC"}
    ).json()["series"]
    assert next(s for s in utc if s["metric"] == "heart_rate")["local_days_with_records"] == 2
    october = client.get(
        "/v1/tools/availability",
        headers=alice,
        params={"timezone": NY, "start_date": "2026-10-01", "end_date": "2026-10-31"},
    ).json()
    assert next(s for s in october["series"] if s["metric"] == "heart_rate")["record_count"] == 1
    assert october["window"]["start_utc"].startswith("2026-10-01T04:00:00")


def test_daily_summary_over_http(client, credentials, record):
    alice = credentials[0]
    upload(
        client,
        alice,
        sample(record, "hr-1", "2026-10-01T12:00:00Z", value=60),
        sample(record, "hr-2", "2026-10-01T13:00:00Z", value=61),
        sample(record, "sdnn", "2026-10-01T12:00:00Z", metric="hrv_sdnn", unit="ms", value=50),
        sample(record, "rmssd", "2026-10-01T12:00:00Z", metric="hrv_rmssd", unit="ms", value=30),
    )
    params = {"timezone": NY, "start_date": "2026-10-01", "end_date": "2026-10-02"}
    heart = client.get(
        "/v1/tools/daily-summary", headers=alice, params=params | {"metric": "heart_rate"}
    ).json()
    first, second = heart["providers"][0]["days"]
    assert (first["sample_count"], first["sample_mean"]) == (2, "60.500000")
    assert second["sample_count"] == 0 and second["sample_mean"] is None
    assert heart["unit"] == "bpm" and heart["metric_class"] == "point"
    assert heart["window"]["end_utc"].startswith("2026-10-03T04:00:00")
    sdnn = client.get(
        "/v1/tools/daily-summary", headers=alice, params=params | {"metric": "hrv_sdnn"}
    ).json()
    assert sdnn["providers"][0]["days"][0]["sample_mean"] == "50.000000"
    assert sdnn["record_count"] == 1
    assert client.get(
        "/v1/tools/daily-summary",
        headers=alice,
        params=params | {"metric": "heart_rate", "provider": "google_health"},
    ).json()["warnings"] == ["no_records_in_window"]


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"end_date": "2027-01-02"}, "at most 93"),  # 94 local days
        ({"end_date": "2026-09-01"}, "precede"),
        ({"timezone": "Mars/Olympus"}, "IANA"),
    ],
)
def test_daily_summary_rejects_unsupported_requests(client, credentials, changes, fragment):
    params = {
        "metric": "steps",
        "timezone": NY,
        "start_date": "2026-10-01",
        "end_date": "2026-10-07",
    } | changes
    response = client.get("/v1/tools/daily-summary", headers=credentials[0], params=params)
    assert response.status_code == 422 and fragment in response.json()["detail"]


def test_record_limit_refuses_instead_of_truncating(client, credentials, record, monkeypatch):
    alice = credentials[0]
    upload(client, alice, *(sample(record, f"hr-{i}", f"2026-10-01T1{i}:00:00Z") for i in range(3)))
    monkeypatch.setattr(queries, "MAX_SUMMARY_RECORDS", 2)
    params = {
        "metric": "heart_rate",
        "timezone": NY,
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
    }
    response = client.get("/v1/tools/daily-summary", headers=alice, params=params)
    assert response.status_code == 422 and "More than 2 records" in response.json()["detail"]
    monkeypatch.setattr(queries, "MAX_SUMMARY_RECORDS", 3)
    assert (
        client.get("/v1/tools/daily-summary", headers=alice, params=params).json()["record_count"]
        == 3
    )


@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"timezone": "Mars/Olympus"}, "IANA"),
        ({"end_date": "2027-01-02"}, "at most 366"),
        ({"end_date": "9999-12-31"}, "between 2000-01-01 and 2100-12-31"),
        ({"start_date": "0001-01-01"}, "between 2000-01-01 and 2100-12-31"),
    ],
)
def test_availability_window_is_bounded(client, credentials, params, fragment):
    base = {"timezone": NY, "start_date": "2026-01-01", "end_date": "2026-12-31"}
    response = client.get("/v1/tools/availability", headers=credentials[0], params=base | params)
    assert response.status_code == 422, response.text
    assert fragment in response.json()["detail"]


def test_availability_requires_a_window(client, credentials):
    response = client.get("/v1/tools/availability", headers=credentials[0], params={"timezone": NY})
    assert response.status_code == 422
    assert {e["loc"][-1] for e in response.json()["detail"]} == {"start_date", "end_date"}


def test_daily_summary_date_overflow_is_422_not_500(client, credentials):
    params = {
        "metric": "steps",
        "timezone": NY,
        "start_date": "2026-10-01",
        "end_date": "9999-12-31",
    }
    response = client.get("/v1/tools/daily-summary", headers=credentials[0], params=params)
    assert response.status_code == 422 and "2100-12-31" in response.json()["detail"]


def interval(record, source_record_id, value, started, ended):
    return record | {
        "source_record_id": source_record_id,
        "metric": "steps",
        "unit": "count",
        "aggregation": "interval_total",
        "value": value,
        "started_at": started,
        "ended_at": ended,
    }


STEPS_DAY = {"metric": "steps", "timezone": NY}


def oct1_window(client, headers, start, end):
    params = STEPS_DAY | {"start_date": start, "end_date": end}
    return client.get("/v1/tools/daily-summary", headers=headers, params=params).json()


def test_overlap_context_is_complete_on_both_sides_of_the_window(client, credentials, record):
    alice = credentials[0]
    upload(
        client,
        alice,
        # A: 23:30 Oct 1 to 00:30 Oct 2 New York; B: 00:00 to 01:00 Oct 2. Overlap 30 minutes.
        interval(record, "a", 100, "2026-10-02T03:30:00Z", "2026-10-02T04:30:00Z"),
        interval(record, "b", 80, "2026-10-02T04:00:00Z", "2026-10-02T05:00:00Z"),
        interval(record, "clean", 10, "2026-10-03T12:00:00Z", "2026-10-03T12:30:00Z"),
    )
    # Same day, three windows: the verdict for Oct 1 and Oct 2 must not depend on the window.
    only_oct1 = oct1_window(client, alice, "2026-10-01", "2026-10-01")["providers"][0]["days"][0]
    assert only_oct1["interval_total_sum"] is None
    assert "overlapping_intervals" in only_oct1["warnings"]
    only_oct2 = oct1_window(client, alice, "2026-10-02", "2026-10-03")
    oct2, oct3 = only_oct2["providers"][0]["days"]
    assert oct2["interval_total_sum"] is None and oct2["warnings"] == ["overlapping_intervals"]
    assert oct3["interval_total_sum"] == "10.000000" and only_oct2["record_count"] == 2
    both = oct1_window(client, alice, "2026-10-01", "2026-10-02")["providers"][0]["days"]
    assert [d["interval_total_sum"] for d in both] == [None, None]


def test_long_interval_starting_days_earlier_is_still_detected(client, credentials, record):
    alice = credentials[0]
    upload(
        client,
        alice,
        # Starts Sep 29 23:00 New York and ends Oct 2 00:30; overlaps B below by 30 minutes.
        interval(record, "long", 500, "2026-09-30T03:00:00Z", "2026-10-02T04:30:00Z"),
        interval(record, "b", 80, "2026-10-02T04:00:00Z", "2026-10-02T05:00:00Z"),
    )
    oct2 = oct1_window(client, alice, "2026-10-02", "2026-10-02")["providers"][0]["days"][0]
    assert oct2["interval_total_sum"] is None and oct2["warnings"] == ["overlapping_intervals"]


def test_overlap_context_counts_against_the_record_budget(client, credentials, record, monkeypatch):
    alice = credentials[0]
    upload(
        client,
        alice,
        interval(record, "in", 80, "2026-10-02T04:00:00Z", "2026-10-02T06:00:00Z"),
        interval(record, "ctx-1", 1, "2026-10-02T03:00:00Z", "2026-10-02T04:30:00Z"),
        interval(record, "ctx-2", 1, "2026-10-02T03:10:00Z", "2026-10-02T04:40:00Z"),
    )
    monkeypatch.setattr(queries, "MAX_SUMMARY_RECORDS", 2)
    response = client.get(
        "/v1/tools/daily-summary",
        headers=alice,
        params=STEPS_DAY | {"start_date": "2026-10-02", "end_date": "2026-10-02"},
    )
    assert response.status_code == 422 and "More than 2 records" in response.json()["detail"]
