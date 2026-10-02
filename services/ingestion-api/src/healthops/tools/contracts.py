"""Typed tool parameters and results. Identity is never part of a tool parameter set."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from healthops.schemas import Aggregation, Metric, Provider

MetricClass = Literal["point", "cumulative"]


class ToolWindow(BaseModel):
    timezone: str
    start_date: date | None
    end_date: date | None
    start_utc: datetime | None
    end_utc: datetime | None


class AvailabilityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(min_length=1, max_length=80)
    start_date: date | None = None
    end_date: date | None = None


class AvailabilitySeries(BaseModel):
    provider: Provider
    metric: Metric
    aggregation: Aggregation
    unit: str
    record_count: int
    first_started_at: datetime
    last_started_at: datetime
    local_days_with_records: int


class AvailabilityResult(BaseModel):
    window: ToolWindow
    series: list[AvailabilitySeries]
    notes: list[str]


class DailySummaryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Metric
    timezone: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date
    provider: Provider | None = None


class DaySummary(BaseModel):
    date: date
    sample_count: int = 0
    sample_min: Decimal | None = None
    sample_max: Decimal | None = None
    sample_mean: Decimal | None = None
    interval_total_count: int = 0
    interval_total_sum: Decimal | None = None
    daily_total_count: int = 0
    daily_total: Decimal | None = None
    daily_summary_count: int = 0
    daily_summary: Decimal | None = None
    unaligned_count: int = 0
    unsupported_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class ProviderDailySummary(BaseModel):
    provider: Provider
    record_count: int
    days: list[DaySummary]


class DailySummaryResult(BaseModel):
    metric: Metric
    unit: str
    metric_class: MetricClass
    window: ToolWindow
    record_count: int
    providers: list[ProviderDailySummary]
    warnings: list[str]
    definitions: dict[str, str]
