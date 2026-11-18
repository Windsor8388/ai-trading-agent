"""Read-only ingestion coordination, quality gates, polling plans, and lineage receipts.

The pipeline has no background thread and no default network provider. A caller must
explicitly invoke `poll_due`; that separation avoids surprise data connections and makes
scheduled behavior deterministic in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from statistics import median
from typing import Callable, Mapping, Sequence

from ..data import DataQualityReport, timeframe_seconds, validate_closed_candles
from ..models import Candle
from .contracts import BarRequest, MarketDataProvider, MarketDataResponse


@dataclass(frozen=True, slots=True)
class DataGap:
    previous_close: datetime
    current_open: datetime
    missing_bars: int


@dataclass(frozen=True, slots=True)
class DataAnomaly:
    open_time: datetime
    category: str
    detail: str


@dataclass(frozen=True, slots=True)
class PipelineQuality:
    integrity: DataQualityReport
    gaps: tuple[DataGap, ...]
    anomalies: tuple[DataAnomaly, ...]
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataPipelinePolicy:
    max_missing_bars: int = 0
    flag_range_multiple_of_median: float = 8.0
    quarantine_on_anomaly: bool = False

    def __post_init__(self) -> None:
        if self.max_missing_bars < 0:
            raise ValueError("max_missing_bars cannot be negative.")
        if self.flag_range_multiple_of_median <= 1:
            raise ValueError("Anomaly range multiple must exceed one.")


@dataclass(frozen=True, slots=True)
class IngestionReceipt:
    provider_name: str
    request: BarRequest
    response: MarketDataResponse | None
    observed_at: datetime
    quality: PipelineQuality | None
    accepted: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PollSchedule:
    schedule_id: str
    provider_name: str
    cadence: timedelta
    next_due_at: datetime


@dataclass(slots=True)
class _RegisteredPoll:
    schedule: PollSchedule
    provider: MarketDataProvider
    request_factory: Callable[[datetime], BarRequest]


def inspect_gaps_and_anomalies(candles: Sequence[Candle], timeframe: str, *, range_multiple: float) -> tuple[tuple[DataGap, ...], tuple[DataAnomaly, ...]]:
    """Classify, but never fill, gaps and unusual completed-bar ranges."""
    seconds = timeframe_seconds(timeframe)
    gaps: list[DataGap] = []
    anomalies: list[DataAnomaly] = []
    ranges: list[float] = []
    for index, candle in enumerate(candles):
        if index:
            previous = candles[index - 1]
            elapsed = (candle.open_time - previous.close_time).total_seconds()
            if elapsed > 0:
                missing = max(1, round(elapsed / seconds))
                gaps.append(DataGap(previous.close_time, candle.open_time, missing))
        current_range = candle.high - candle.low
        baseline = median([item for item in ranges[-20:] if item > 0]) if any(item > 0 for item in ranges[-20:]) else None
        if baseline and current_range > baseline * range_multiple:
            anomalies.append(DataAnomaly(
                candle.open_time, "RANGE_OUTLIER",
                f"range={current_range:.8g} exceeds {range_multiple:g}x trailing median={baseline:.8g}",
            ))
        ranges.append(current_range)
    return tuple(gaps), tuple(anomalies)


class MarketDataPipeline:
    """Accepts only identity-consistent, complete, quality-gated provider output."""

    version = "0.1.0"

    def __init__(self, policy: DataPipelinePolicy | None = None) -> None:
        self.policy = policy or DataPipelinePolicy()
        self._receipts: list[IngestionReceipt] = []
        self._latest_accepted: dict[tuple[str, str, str, str], IngestionReceipt] = {}

    def receipts(self) -> tuple[IngestionReceipt, ...]:
        return tuple(self._receipts)

    def latest_accepted(self, provider: str, instrument: str, timeframe: str, price_component: str) -> IngestionReceipt | None:
        return self._latest_accepted.get((provider, instrument, timeframe, price_component))

    def ingest(self, provider: MarketDataProvider, request: BarRequest, *, observed_at: datetime) -> IngestionReceipt:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware.")
        try:
            response = provider.fetch_completed_bars(request)
            if response.feed.provider != provider.name:
                raise ValueError("Provider response lineage name does not match provider contract.")
            if response.feed.instrument != request.instrument or response.feed.price_component != request.price_component:
                raise ValueError("Provider response identity does not match requested instrument/component.")
            integrity = validate_closed_candles(response.candles, observed_at)
            gaps, anomalies = inspect_gaps_and_anomalies(
                response.candles, request.timeframe, range_multiple=self.policy.flag_range_multiple_of_median,
            )
            reasons = list(integrity.errors)
            if sum(item.missing_bars for item in gaps) > self.policy.max_missing_bars:
                reasons.append(f"Missing-bar count exceeds policy: {sum(item.missing_bars for item in gaps)} > {self.policy.max_missing_bars}.")
            if anomalies and self.policy.quarantine_on_anomaly:
                reasons.append(f"Anomaly quarantine: {len(anomalies)} completed-bar anomaly/anomalies.")
            quality = PipelineQuality(integrity, gaps, anomalies, not reasons, tuple(reasons))
            receipt = IngestionReceipt(provider.name, request, response, observed_at, quality, quality.accepted)
        except Exception as exc:
            receipt = IngestionReceipt(provider.name, request, None, observed_at, None, False, f"{type(exc).__name__}: {exc}")
        self._receipts.append(receipt)
        if receipt.accepted:
            self._latest_accepted[(provider.name, request.instrument, request.timeframe, request.price_component)] = receipt
        return receipt


class PeriodicPollingCoordinator:
    """Deterministic polling plan; no daemon is started by constructing this object."""

    def __init__(self, pipeline: MarketDataPipeline) -> None:
        self.pipeline = pipeline
        self._schedules: dict[str, _RegisteredPoll] = {}

    def register(
        self,
        schedule_id: str,
        provider: MarketDataProvider,
        request_factory: Callable[[datetime], BarRequest],
        *,
        cadence: timedelta,
        first_due_at: datetime,
    ) -> PollSchedule:
        if not schedule_id or schedule_id.strip() != schedule_id:
            raise ValueError("schedule_id must be a non-empty trimmed string.")
        if schedule_id in self._schedules:
            raise ValueError(f"Poll schedule already exists: {schedule_id}")
        if cadence.total_seconds() <= 0 or first_due_at.tzinfo is None:
            raise ValueError("Poll cadence must be positive and first_due_at timezone-aware.")
        schedule = PollSchedule(schedule_id, provider.name, cadence, first_due_at)
        self._schedules[schedule_id] = _RegisteredPoll(schedule, provider, request_factory)
        return schedule

    def schedules(self) -> tuple[PollSchedule, ...]:
        return tuple(item.schedule for item in self._schedules.values())

    def poll_due(self, now: datetime) -> tuple[IngestionReceipt, ...]:
        if now.tzinfo is None:
            raise ValueError("Polling timestamp must be timezone-aware.")
        results: list[IngestionReceipt] = []
        for schedule_id, registered in tuple(self._schedules.items()):
            if registered.schedule.next_due_at > now:
                continue
            request = registered.request_factory(now)
            results.append(self.pipeline.ingest(registered.provider, request, observed_at=now))
            next_due = registered.schedule.next_due_at
            while next_due <= now:
                next_due += registered.schedule.cadence
            self._schedules[schedule_id] = _RegisteredPoll(
                PollSchedule(schedule_id, registered.provider.name, registered.schedule.cadence, next_due),
                registered.provider,
                registered.request_factory,
            )
        return tuple(results)
