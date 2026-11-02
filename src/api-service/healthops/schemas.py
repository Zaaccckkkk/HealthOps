from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class Metric(StrEnum):
    HEART_RATE = "heart_rate"
    RESTING_HEART_RATE = "resting_heart_rate"
    STEPS = "steps"
    HRV_SDNN = "hrv_sdnn"
    HRV_RMSSD = "hrv_rmssd"
    SLEEP_DURATION = "sleep_duration"
    ACTIVE_ENERGY = "active_energy"
    EXERCISE_DURATION = "exercise_duration"


UNITS = {
    Metric.HEART_RATE: "bpm",
    Metric.RESTING_HEART_RATE: "bpm",
    Metric.STEPS: "count",
    Metric.HRV_SDNN: "ms",
    Metric.HRV_RMSSD: "ms",
    Metric.SLEEP_DURATION: "s",
    Metric.ACTIVE_ENERGY: "kcal",
    Metric.EXERCISE_DURATION: "s",
}
Provider = Literal["healthkit", "google_health", "manual"]
Aggregation = Literal["sample", "interval_total", "daily_total", "daily_summary"]


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Provider
    source_record_id: str = Field(min_length=1, max_length=300)
    source_device: str | None = Field(default=None, min_length=1, max_length=150)
    metric: Metric
    value: Decimal = Field(ge=0, max_digits=20, decimal_places=6, allow_inf_nan=False)
    unit: str = Field(max_length=12)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    source_timezone: str = Field(min_length=1, max_length=80)
    aggregation: Aggregation

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("A boolean is not a measurement.")
        return value

    @field_validator("started_at", "ended_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("source_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Expected an IANA timezone such as America/New_York.") from exc
        return value

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        if self.unit != UNITS[self.metric]:
            raise ValueError("Metric and canonical unit do not match.")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at.")
        if self.metric == Metric.STEPS and self.value != self.value.to_integral_value():
            raise ValueError("Step counts must be integers.")
        if self.metric in (Metric.HEART_RATE, Metric.RESTING_HEART_RATE) and self.value == 0:
            raise ValueError("Missing heart rate must not be encoded as zero.")
        return self


class ObservationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: Annotated[list[ObservationIn], Field(min_length=1, max_length=500)]


class ObservationOut(ObservationIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ingested_at: datetime


class BatchResult(BaseModel):
    received: int
    inserted: int
    duplicates: int


class ObservationPage(BaseModel):
    items: list[ObservationOut]
    next_offset: int | None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
