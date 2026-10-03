"""Google Health API connector: OAuth helpers, data-point mapping and windowed sync.

Facts this module relies on (verified against the public reference on 2026-10-03):
- Authorization code flow against Google OAuth; refresh tokens need ``access_type=offline``.
- ``GET {base}/users/me/identity`` returns ``healthUserId`` / ``legacyUserId`` and answers
  400 ``ACCOUNT_NOT_LINKED`` when the Google Account has no Google Health link.
- ``GET {base}/users/me/dataTypes/{type}/dataPoints`` lists points with ``filter`` and
  ``pageToken``; ``POST .../dataPoints:dailyRollUp`` returns per-day values with a single
  rollup field named after the type (``steps`` -> ``countSum`` etc.).
- A data point's ``name`` is empty for most types, so record identities are built from the
  type and its own time fields.

Mapping choices (provider ``google_health``), checked against a real account on 2026-10-03:
- steps: intraday interval points (one-minute intervals, only minutes with steps, about 20 to
  50 per day for a Fitbit tracker) -> ``interval_total``; the tools sum them per local day.
  ``dailyRollUp`` returned no points for this account and rejected every documented
  ``dataSourceFamily`` value, so roll-ups are not used.
- heart rate: not synced. Fitbit heart-rate samples arrive every few seconds (hundreds of
  pages per day), far beyond the tools' record budget, and roll-ups are unavailable.
- daily resting heart rate -> ``daily_summary`` on ``resting_heart_rate``.
- daily heart rate variability: ``averageHeartRateVariabilityMilliseconds`` ->
  ``daily_summary`` on ``hrv_rmssd`` (kept apart from HealthKit's SDNN).
- sleep logs: ``summary.minutesAsleep`` * 60 -> ``interval_total`` on ``sleep_duration``.
Only points with ``dataSource.platform == FITBIT`` are kept: Google Health also holds copies
of Apple Health data, which already reach this system through the HealthKit channel.
Active energy and active minutes are deliberately not mapped until their response fields
have been checked against a real account.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from healthops.schemas import ObservationIn

PROVIDER = "google_health"
SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
)
# Data types this connector syncs, in the order they run.
DATA_TYPES = (
    "steps",
    "daily-resting-heart-rate",
    "daily-heart-rate-variability",
    "sleep",
)
# Google Health also holds data the Google Health app copied *from* Apple Health
# (``platform: HEALTH_KIT``). That data already reaches this system through the HealthKit
# channel, so the Google channel keeps only points recorded by Google/Fitbit devices.
ACCEPTED_PLATFORMS = ("FITBIT",)


class GoogleApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Google API error {status}: {message}")
        self.status = status
        self.message = message


class AccountNotLinked(GoogleApiError):
    """The Google Account exists but is not linked to Google Health."""


@dataclass
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    authorize_url: str
    token_url: str
    revoke_url: str
    api_base_url: str


class GoogleHealthClient:
    """Thin synchronous HTTP client. ``transport`` lets tests inject a fake Google."""

    def __init__(self, config: GoogleOAuthConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._http = httpx.Client(timeout=30.0, transport=transport)

    # --- OAuth -------------------------------------------------------------------------
    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{self.config.authorize_url}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        response = self._http.post(
            self.config.token_url,
            data={
                "code": code,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        return _json_or_error(response)

    def refresh_access_token(self, refresh_token: str) -> str:
        response = self._http.post(
            self.config.token_url,
            data={
                "refresh_token": refresh_token,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "refresh_token",
            },
        )
        return _json_or_error(response)["access_token"]

    def revoke(self, token: str) -> bool:
        response = self._http.post(self.config.revoke_url, data={"token": token})
        return response.status_code == 200

    # --- Google Health -----------------------------------------------------------------
    def identity(self, access_token: str) -> dict[str, Any]:
        response = self._http.get(
            f"{self.config.api_base_url}/users/me/identity", headers=_auth(access_token)
        )
        if response.status_code == 400 and "ACCOUNT_NOT_LINKED" in response.text:
            raise AccountNotLinked(400, "ACCOUNT_NOT_LINKED")
        return _json_or_error(response)

    def list_data_points(self, access_token: str, data_type: str, filter_text: str, page_size: int):
        """Yields every data point of every page; raises on the first failed page."""
        token = None
        while True:
            params: dict[str, Any] = {"pageSize": page_size, "filter": filter_text}
            if token:
                params["pageToken"] = token
            response = self._http.get(
                f"{self.config.api_base_url}/users/me/dataTypes/{data_type}/dataPoints",
                params=params,
                headers=_auth(access_token),
            )
            body = _json_or_error(response)
            yield from body.get("dataPoints", [])
            token = body.get("nextPageToken")
            if not token:
                return


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", response.reason_phrase)
        except Exception:
            message = response.reason_phrase or "request failed"
        raise GoogleApiError(response.status_code, str(message)[:200])
    return response.json()


def platform_of(point: dict[str, Any]) -> str | None:
    value = (point.get("dataSource") or {}).get("platform")
    return str(value) if value else None


def accepted(point: dict[str, Any]) -> bool:
    """Only points recorded on an accepted platform are ingested; unknown platforms are not."""
    return platform_of(point) in ACCEPTED_PLATFORMS


def _device_label(point: dict[str, Any]) -> str | None:
    device = (point.get("dataSource") or {}).get("device") or {}
    label = device.get("displayName") or device.get("manufacturer")
    return str(label)[:150] if label else None


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _date(obj: dict[str, Any]) -> date:
    return date(int(obj["year"]), int(obj["month"]), int(obj["day"]))


def _platform_suffix(point: dict[str, Any]) -> str:
    return platform_of(point) or "unknown"


def _pick(obj: dict[str, Any], *names: str):
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return None


def _local_day(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def map_steps_interval(point: dict[str, Any], tz: ZoneInfo) -> ObservationIn | None:
    body = point.get("steps") or {}
    interval = body.get("interval") or {}
    if "count" not in body or "startTime" not in interval or "endTime" not in interval:
        return None
    start, end = _ts(interval["startTime"]), _ts(interval["endTime"])
    return ObservationIn(
        provider=PROVIDER,
        source_record_id=f"steps:{start.isoformat()}:{end.isoformat()}:{_platform_suffix(point)}",
        source_device=_device_label(point),
        metric="steps",
        value=Decimal(str(body["count"])),
        unit="count",
        started_at=start,
        ended_at=end,
        source_timezone=tz.key,
        aggregation="interval_total",
    )


def map_resting_heart_rate(point: dict[str, Any], tz: ZoneInfo) -> ObservationIn | None:
    body = point.get("dailyRestingHeartRate") or {}
    if "beatsPerMinute" not in body:
        return None
    day = _date(body["date"])
    start, end = _local_day(day, tz)
    return ObservationIn(
        provider=PROVIDER,
        source_record_id=f"daily-resting-heart-rate:{day.isoformat()}:{_platform_suffix(point)}",
        source_device=_device_label(point),
        metric="resting_heart_rate",
        value=Decimal(str(body["beatsPerMinute"])),
        unit="bpm",
        started_at=start,
        ended_at=end,
        source_timezone=tz.key,
        aggregation="daily_summary",
    )


def map_hrv(point: dict[str, Any], tz: ZoneInfo) -> ObservationIn | None:
    body = point.get("dailyHeartRateVariability") or {}
    value = body.get("averageHeartRateVariabilityMilliseconds")
    if value is None:
        return None
    day = _date(body["date"])
    start, end = _local_day(day, tz)
    return ObservationIn(
        provider=PROVIDER,
        source_record_id=f"daily-heart-rate-variability:{day.isoformat()}:{_platform_suffix(point)}",
        source_device=_device_label(point),
        metric="hrv_rmssd",
        value=Decimal(str(value)).quantize(Decimal("0.000001")),
        unit="ms",
        started_at=start,
        ended_at=end,
        source_timezone=tz.key,
        aggregation="daily_summary",
    )


def map_sleep(point: dict[str, Any], tz: ZoneInfo) -> ObservationIn | None:
    body = point.get("sleep") or {}
    interval, summary = body.get("interval") or {}, body.get("summary") or {}
    if "minutesAsleep" not in summary or "startTime" not in interval or "endTime" not in interval:
        return None
    start, end = _ts(interval["startTime"]), _ts(interval["endTime"])
    return ObservationIn(
        provider=PROVIDER,
        source_record_id=f"sleep:{start.isoformat()}:{end.isoformat()}:{_platform_suffix(point)}",
        source_device=_device_label(point),
        metric="sleep_duration",
        value=Decimal(int(summary["minutesAsleep"]) * 60),
        unit="s",
        started_at=start,
        ended_at=end,
        source_timezone=tz.key,
        aggregation="interval_total",
    )


LIST_MAPPERS = {
    "steps": (
        map_steps_interval,
        lambda s, e: (
            f'steps.interval.start_time >= "{_start_of(s)}" AND '
            f'steps.interval.start_time < "{_start_of(e)}"'
        ),
        2000,
    ),
    "daily-resting-heart-rate": (
        map_resting_heart_rate,
        lambda s, e: (
            f'daily_resting_heart_rate.date >= "{s.isoformat()}" AND '
            f'daily_resting_heart_rate.date < "{e.isoformat()}"'
        ),
        100,
    ),
    "daily-heart-rate-variability": (
        map_hrv,
        lambda s, e: (
            f'daily_heart_rate_variability.date >= "{s.isoformat()}" AND '
            f'daily_heart_rate_variability.date < "{e.isoformat()}"'
        ),
        100,
    ),
    "sleep": (
        map_sleep,
        lambda s, e: (
            f'sleep.interval.end_time >= "{_start_of(s)}" AND '
            f'sleep.interval.end_time < "{_start_of(e)}"'
        ),
        25,
    ),
}


def _start_of(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC).isoformat().replace("+00:00", "Z")


# --- sync ----------------------------------------------------------------------------------


@dataclass
class TypeSyncReport:
    data_type: str
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    conflicts: list[str] = field(default_factory=list)
    unmapped: int = 0
    # Points from other platforms (for example Apple Health copies inside Google Health).
    skipped_other_platform: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    watermark: str | None = None


@dataclass
class SyncReport:
    window_start: str
    window_end: str
    timezone: str
    types: list[TypeSyncReport] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "window_start": self.window_start,
                "window_end": self.window_end,
                "timezone": self.timezone,
                "types": [t.__dict__ for t in self.types],
            }
        )


def sync_window(
    client: GoogleHealthClient,
    access_token: str,
    ingest,
    window_start: date,
    window_end: date,
    tz: ZoneInfo,
    cursors: dict[str, str],
) -> SyncReport:
    """Fetch and ingest every data type for [window_start, window_end].

    ``ingest(records) -> ConnectorIngestResult`` is supplied by the caller. A type's cursor
    (watermark) only moves when every page of its window succeeded; a failed page leaves the
    cursor where it was and records the error in the report, so a partial window is never
    reported as complete.
    """
    report = SyncReport(window_start.isoformat(), window_end.isoformat(), tz.key)
    end_exclusive = window_end + timedelta(days=1)
    for data_type in DATA_TYPES:
        item = TypeSyncReport(data_type=data_type)
        try:
            records: list[ObservationIn] = []
            mapper, filter_builder, page_size = LIST_MAPPERS[data_type]
            points = client.list_data_points(
                access_token, data_type, filter_builder(window_start, end_exclusive), page_size
            )
            for point in points:
                item.fetched += 1
                if not accepted(point):
                    item.skipped_other_platform += 1
                    continue
                mapped = mapper(point, tz)
                if mapped is None:
                    item.unmapped += 1
                else:
                    records.append(mapped)
            result = ingest(records)
            item.inserted, item.duplicates = result.inserted, result.duplicates
            item.conflicts = list(result.conflicts)
            cursors[data_type] = end_exclusive.isoformat()
            item.watermark = cursors[data_type]
        except GoogleApiError as exc:
            item.error = f"google {exc.status}: {exc.message}"
            item.watermark = cursors.get(data_type)
        except (KeyError, ValueError, TypeError) as exc:
            item.error = f"unexpected response shape: {type(exc).__name__}"
            item.watermark = cursors.get(data_type)
        report.types.append(item)
    return report


def new_state() -> str:
    return secrets.token_urlsafe(32)
