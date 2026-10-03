"""A fake Google OAuth + Google Health API for tests, served through httpx.MockTransport.

Synthetic data only. Shapes follow the public reference (dataPoints list with pagination,
dailyRollUp with civil times, identity endpoint, token and revoke endpoints).
"""

import json
from urllib.parse import parse_qs

import httpx

API = "https://fake-health.test/v4"
TOKEN_URL = "https://fake-oauth.test/token"
REVOKE_URL = "https://fake-oauth.test/revoke"
AUTHORIZE_URL = "https://fake-oauth.test/auth"


def steps_interval(start: str, end: str, count: int, platform: str = "FITBIT"):
    return {
        "steps": {"interval": {"startTime": start, "endTime": end}, "count": str(count)},
        "dataSource": {"platform": platform, "device": {"displayName": "Synthetic Tracker"}},
    }


def resting(day: str, bpm: int, platform: str = "FITBIT"):
    y, m, d = (int(x) for x in day.split("-"))
    return {
        "dailyRestingHeartRate": {
            "date": {"year": y, "month": m, "day": d},
            "beatsPerMinute": str(bpm),
        },
        "dataSource": {"platform": platform, "device": {"displayName": "Synthetic Tracker"}},
    }


def hrv(day: str, ms: float):
    y, m, d = (int(x) for x in day.split("-"))
    return {
        "dailyHeartRateVariability": {
            "date": {"year": y, "month": m, "day": d},
            "averageHeartRateVariabilityMilliseconds": ms,
            "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": ms + 3,
        },
        "dataSource": {"platform": "FITBIT", "device": {"displayName": "Synthetic Tracker"}},
    }


def sleep(start: str, end: str, minutes: int):
    return {
        "name": f"users/me/dataTypes/sleep/dataPoints/{start}",
        "sleep": {
            "interval": {"startTime": start, "endTime": end},
            "summary": {
                "minutesAsleep": str(minutes),
                "minutesAwake": "20",
                "minutesInSleepPeriod": str(minutes + 20),
            },
        },
        "dataSource": {"platform": "FITBIT", "device": {"displayName": "Synthetic Tracker"}},
    }


class FakeGoogle:
    """Mutable fake: tests change ``data`` to simulate revisions and ``fail_type`` for errors."""

    def __init__(self):
        self.data = {
            "steps": [
                steps_interval("2026-10-01T13:00:00Z", "2026-10-01T13:01:00Z", 64),
                steps_interval("2026-10-02T14:00:00Z", "2026-10-02T14:01:00Z", 71),
                steps_interval(
                    "2026-10-02T15:00:00Z", "2026-10-02T15:01:00Z", 30, platform="HEALTH_KIT"
                ),
            ],
            # The HEALTH_KIT point is an Apple Health copy inside Google Health; it is skipped.
            "daily-resting-heart-rate": [
                resting("2026-10-01", 58),
                resting("2026-10-01", 61, platform="HEALTH_KIT"),
                resting("2026-10-02", 57),
            ],
            "daily-heart-rate-variability": [hrv("2026-10-01", 42.5)],
            "sleep": [
                sleep("2026-10-01T03:00:00Z", "2026-10-01T10:30:00Z", 410),
                sleep("2026-10-02T03:10:00Z", "2026-10-02T10:00:00Z", 380),
            ],
        }
        self.page_size_override = {"sleep": 1}  # forces two pages for sleep
        self.fail_type: str | None = None  # data type whose second page fails
        self.identity = {"healthUserId": "health-user-synthetic", "legacyUserId": "LEGACY1"}
        self.account_linked = True
        self.refresh_valid = True
        self.revoked: list[str] = []
        self.requests: list[str] = []
        self.reject_source_family = False  # simulate an API that rejects the family value
        self.rollup_bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(f"{request.method} {request.url.path}")
        if url.startswith(TOKEN_URL):
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["authorization_code"]:
                if form.get("code") == ["bad-code"]:
                    return httpx.Response(
                        400, json={"error": "invalid_grant", "error_description": "bad"}
                    )
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "expires_in": 3599,
                        "scope": "scope-a scope-b",
                    },
                )
            if not self.refresh_valid:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3599})
        if url.startswith(REVOKE_URL):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200, json={})
        if url.startswith(API + "/users/me/identity"):
            if not self.account_linked:
                return httpx.Response(
                    400, json={"error": {"status": "ACCOUNT_NOT_LINKED", "message": "not linked"}}
                )
            return httpx.Response(200, json=self.identity)
        if "/dataTypes/" in url:
            data_type = url.split("/dataTypes/")[1].split("/")[0]
            points = self.data.get(data_type, [])
            if url.endswith(":dailyRollUp"):
                body = json.loads(request.content.decode())
                self.rollup_bodies.append(body)
                if self.reject_source_family and body.get("dataSourceFamily"):
                    return httpx.Response(
                        400, json={"error": {"message": "Invalid value at 'data_source_family'"}}
                    )
                if "date" not in body["range"]["start"]:
                    return httpx.Response(
                        400, json={"error": {"message": "Unknown name year at range.start"}}
                    )
                page = body.get("pageToken")
            else:
                page = request.url.params.get("pageToken")
            size = self.page_size_override.get(data_type, 1000)
            start = int(page or 0)
            if self.fail_type == data_type and start > 0:
                return httpx.Response(503, json={"error": {"message": "backend unavailable"}})
            chunk = points[start : start + size]
            body = {"dataPoints": chunk}
            if start + size < len(points):
                body["nextPageToken"] = str(start + size)
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": "unknown fake endpoint"}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
