"""Synthetic observation aggregation regressions; no model, database or health export."""

import pandas as pd
import pytest

from training.data import _daily_readings


def observations(*rows):
    """Rows contain user, metric, value, UTC start and source time zone."""
    return pd.DataFrame(
        rows, columns=["user_id", "metric", "value", "started_at", "source_timezone"]
    )


@pytest.mark.parametrize("values, expected", [([60, 70], 65), ([61, 70], 65.5), ([62], 62)])
def test_resting_heart_rate_is_sample_mean(values, expected):
    obs = observations(
        *[
            ("synthetic-a", "resting_heart_rate", value, f"2026-06-01T{12 + i}:00:00Z", "UTC")
            for i, value in enumerate(values)
        ]
    )
    result = _daily_readings(obs, pd.DataFrame())
    assert len(result) == 1
    assert result.iloc[0]["metric"] == "rhr"
    assert result.iloc[0]["value"] == pytest.approx(expected)


def test_steps_remain_additive_when_heart_rate_is_averaged():
    obs = observations(
        ("synthetic-a", "steps", 100, "2026-06-01T12:00:00Z", "UTC"),
        ("synthetic-a", "steps", 200, "2026-06-01T13:00:00Z", "UTC"),
        ("synthetic-a", "resting_heart_rate", 60, "2026-06-01T12:00:00Z", "UTC"),
        ("synthetic-a", "resting_heart_rate", 70, "2026-06-01T13:00:00Z", "UTC"),
    )
    result = _daily_readings(obs, pd.DataFrame()).set_index("metric")
    assert result.loc["steps", "value"] == 300
    assert result.loc["rhr", "value"] == 65


def test_mean_keeps_users_and_source_local_days_separate():
    zone = "America/New_York"
    obs = observations(
        ("synthetic-a", "resting_heart_rate", 60, "2026-06-01T23:00:00Z", zone),
        ("synthetic-a", "resting_heart_rate", 70, "2026-06-02T03:00:00Z", zone),
        ("synthetic-a", "resting_heart_rate", 80, "2026-06-02T04:00:00Z", zone),
        ("synthetic-b", "resting_heart_rate", 90, "2026-06-01T23:00:00Z", zone),
    )
    result = _daily_readings(obs, pd.DataFrame()).set_index(["user_id", "date"])
    assert len(result) == 3
    assert result.loc[("synthetic-a", pd.Timestamp("2026-06-01")), "value"] == 65
    assert result.loc[("synthetic-a", pd.Timestamp("2026-06-02")), "value"] == 80
    assert result.loc[("synthetic-b", pd.Timestamp("2026-06-01")), "value"] == 90


def test_sleep_segments_still_sum_as_minutes_without_daytime_records():
    obs = observations(
        ("synthetic-a", "sleep_duration", 3600, "2026-06-01T23:00:00Z", "UTC"),
        ("synthetic-a", "sleep_duration", 1800, "2026-06-02T00:00:00Z", "UTC"),
    )
    beds = pd.DataFrame(
        {
            "user_id": ["synthetic-a"],
            "date": [pd.Timestamp("2026-06-01")],
            "bed_at": pd.to_datetime(["2026-06-01T23:00:00Z"], utc=True).as_unit("ns"),
        }
    )
    result = _daily_readings(obs, beds)
    assert len(result) == 1
    assert result.iloc[0]["metric"] == "sleep"
    assert result.iloc[0]["value"] == 90


def test_absent_readings_do_not_create_zero_measurements():
    result = _daily_readings(observations(), pd.DataFrame())
    assert result.empty
    assert set(result.columns) == {"user_id", "date", "metric", "value"}
