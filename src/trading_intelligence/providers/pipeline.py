"""Read-only ingestion coordination, quality gates, polling plans, and lineage receipts.

The pipeline has no background thread and no default network provider. A caller must
explicitly invoke ``poll_due``; that separation avoids surprise data connections and
makes scheduled behavior deterministic in tests.  Raw provider responses remain on each
receipt while the normalized store keeps only exact, completed, identity-consistent bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Callable, Mapping, Sequence

from ..data import DataQualityReport, timeframe_seconds, validate_closed_candles
from ..models import Candle, DataMode, FeedMetadata, MarketSnapshot
from .contracts import BarRequest, MarketDataProvider, MarketDataResponse
from .instruments import InstrumentRegistry


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
class DataDuplicate:
    """A replay or conflicting revision observed across accepted provider responses."""

    open_time: datetime
    category: str  # EXACT_REPLAY or CONFLICTING_REVISION
    detail: str


@dataclass(frozen=True, slots=True)
class PipelineQuality:
    integrity: DataQualityReport
    gaps: tuple[DataGap, ...]
    anomalies: tuple[DataAnomaly, ...]
    duplicates: tuple[DataDuplicate, ...]
    freshness_seconds: float | None
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataPipelinePolicy:
    max_missing_bars: int = 0
    flag_range_multiple_of_median: float = 8.0
    quarantine_on_anomaly: bool = False
    max_live_staleness: timedelta | None = None
    reject_conflicting_duplicates: bool = True

    def __post_init__(self) -> None:
        if self.max_missing_bars < 0:
            raise ValueError("max_missing_bars cannot be negative.")
        if self.flag_range_multiple_of_median <= 1:
            raise ValueError("Anomaly range multiple must exceed one.")
        if self.max_live_staleness is not None and self.max_live_staleness.total_seconds() <= 0:
            raise ValueError("max_live_staleness must be positive when configured.")


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


IdentityKey = tuple[str, str, str, str]


def _candle_fingerprint(candle: Candle) -> tuple[datetime, datetime, float, float, float, float, float | None]:
    return (
        candle.open_time, candle.close_time, candle.open, candle.high,
        candle.low, candle.close, candle.volume,
    )


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
    """Accept only complete, identity-consistent, quality-gated provider output.

    Exact overlapping bars are recorded as ``EXACT_REPLAY`` and do not become duplicate
    normalized bars.  Same identity/time with different values is an auditable conflict;
    it is quarantined by default and never silently overwrites the accepted observation.
    """

    version = "0.2.0"

    def __init__(
        self,
        policy: DataPipelinePolicy | None = None,
        *,
        instrument_registry: InstrumentRegistry | None = None,
    ) -> None:
        self.policy = policy or DataPipelinePolicy()
        self.instrument_registry = instrument_registry
        self._receipts: list[IngestionReceipt] = []
        self._latest_accepted: dict[IdentityKey, IngestionReceipt] = {}
        self._normalized: dict[IdentityKey, dict[datetime, Candle]] = {}

    def receipts(self) -> tuple[IngestionReceipt, ...]:
        return tuple(self._receipts)

    def latest_accepted(self, provider: str, instrument: str, timeframe: str, price_component: str) -> IngestionReceipt | None:
        return self._latest_accepted.get((provider, instrument, timeframe, price_component))

    def normalized_bars(self, provider: str, instrument: str, timeframe: str, price_component: str) -> tuple[Candle, ...]:
        """Return exact-deduplicated bars after accepted ingestion, in UTC order.

        This is intentionally not a gap filler.  A snapshot builder will still reject a
        missing requested timeframe and reports only accepted data.
        """
        values = self._normalized.get((provider, instrument, timeframe, price_component), {})
        return tuple(values[open_time] for open_time in sorted(values))

    def _validate_registry_identity(self, response: MarketDataResponse) -> None:
        if self.instrument_registry is None:
            return
        definition = self.instrument_registry.get(response.feed.instrument)
        if definition.instrument_kind != response.feed.instrument_kind:
            raise ValueError(
                "Provider instrument kind does not match canonical registry: "
                f"{response.feed.instrument_kind} != {definition.instrument_kind}."
            )

    def _classify_replays(self, key: IdentityKey, candles: Sequence[Candle]) -> tuple[DataDuplicate, ...]:
        existing = self._normalized.get(key, {})
        duplicates: list[DataDuplicate] = []
        for candle in candles:
            prior = existing.get(candle.open_time)
            if prior is None:
                continue
            if _candle_fingerprint(prior) == _candle_fingerprint(candle):
                duplicates.append(DataDuplicate(candle.open_time, "EXACT_REPLAY", "Provider replayed an already accepted identical completed bar."))
            else:
                duplicates.append(DataDuplicate(
                    candle.open_time, "CONFLICTING_REVISION",
                    "Provider returned different OHLCV values for an already accepted identity/time; no overwrite occurred.",
                ))
        return tuple(duplicates)

    def _freshness(self, response: MarketDataResponse, observed_at: datetime) -> float | None:
        if response.feed.mode != DataMode.LIVE:
            return None
        latest_close = max(candle.close_time for candle in response.candles)
        return max(0.0, (observed_at - latest_close).total_seconds())

    def _commit_unique(self, key: IdentityKey, candles: Sequence[Candle]) -> None:
        destination = self._normalized.setdefault(key, {})
        for candle in candles:
            # Conflicts cannot reach this method. Exact replays retain the first accepted
            # value/provenance and do not need a second normalized record.
            destination.setdefault(candle.open_time, candle)

    def ingest(self, provider: MarketDataProvider, request: BarRequest, *, observed_at: datetime) -> IngestionReceipt:
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at must be timezone-aware UTC.")
        try:
            response = provider.fetch_completed_bars(request)
            if response.feed.provider != provider.name:
                raise ValueError("Provider response lineage name does not match provider contract.")
            if response.feed.instrument != request.instrument or response.feed.price_component != request.price_component:
                raise ValueError("Provider response identity does not match requested instrument/component.")
            self._validate_registry_identity(response)
            integrity = validate_closed_candles(response.candles, observed_at)
            gaps, anomalies = inspect_gaps_and_anomalies(
                response.candles, request.timeframe, range_multiple=self.policy.flag_range_multiple_of_median,
            )
            key = (provider.name, request.instrument, request.timeframe, request.price_component)
            duplicates = self._classify_replays(key, response.candles)
            freshness_seconds = self._freshness(response, observed_at)
            reasons = list(integrity.errors)
            if sum(item.missing_bars for item in gaps) > self.policy.max_missing_bars:
                reasons.append(f"Missing-bar count exceeds policy: {sum(item.missing_bars for item in gaps)} > {self.policy.max_missing_bars}.")
            if anomalies and self.policy.quarantine_on_anomaly:
                reasons.append(f"Anomaly quarantine: {len(anomalies)} completed-bar anomaly/anomalies.")
            conflicts = [item for item in duplicates if item.category == "CONFLICTING_REVISION"]
            if conflicts and self.policy.reject_conflicting_duplicates:
                reasons.append(f"Conflicting provider revisions require quarantine: {len(conflicts)}.")
            if self.policy.max_live_staleness is not None and freshness_seconds is not None:
                maximum = self.policy.max_live_staleness.total_seconds()
                if freshness_seconds > maximum:
                    reasons.append(f"Live feed is stale: {freshness_seconds:.0f}s > policy {maximum:.0f}s.")
            quality = PipelineQuality(integrity, gaps, anomalies, duplicates, freshness_seconds, not reasons, tuple(reasons))
            receipt = IngestionReceipt(provider.name, request, response, observed_at, quality, quality.accepted)
            if receipt.accepted:
                self._commit_unique(key, response.candles)
        except Exception as exc:
            receipt = IngestionReceipt(provider.name, request, None, observed_at, None, False, f"{type(exc).__name__}: {exc}")
        self._receipts.append(receipt)
        if receipt.accepted:
            self._latest_accepted[(provider.name, request.instrument, request.timeframe, request.price_component)] = receipt
        return receipt

    def build_snapshot(
        self,
        provider: str,
        instrument: str,
        price_component: str,
        timeframes: Sequence[str],
        *,
        as_of: datetime,
    ) -> MarketSnapshot:
        """Build an engine snapshot only from accepted, exact-deduplicated bars.

        The caller must explicitly choose provider/component/timeframes.  This avoids
        accidental cross-venue joins and refuses a snapshot when any requested series
        has not passed the ingestion gate.
        """
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError("Snapshot as_of must be timezone-aware UTC.")
        if not timeframes:
            raise ValueError("Snapshot needs at least one requested timeframe.")
        feeds: list[FeedMetadata] = []
        bars_by_timeframe: dict[str, tuple[Candle, ...]] = {}
        for timeframe in dict.fromkeys(timeframes):
            receipt = self.latest_accepted(provider, instrument, timeframe, price_component)
            if receipt is None or receipt.response is None:
                raise ValueError(f"No accepted provider receipt for {provider}/{instrument}/{timeframe}/{price_component}.")
            bars = tuple(bar for bar in self.normalized_bars(provider, instrument, timeframe, price_component) if bar.close_time <= as_of)
            quality = validate_closed_candles(bars, as_of)
            if not quality.passed:
                raise ValueError(f"Accepted bars cannot form {timeframe} snapshot: {'; '.join(quality.errors)}")
            bars_by_timeframe[timeframe] = bars
            feeds.append(receipt.response.feed)
        anchor = feeds[0]
        if any(
            feed.instrument_kind != anchor.instrument_kind or feed.mode != anchor.mode or feed.vendor_symbol != anchor.vendor_symbol
            for feed in feeds[1:]
        ):
            raise ValueError("Requested timeframes do not have a consistent feed identity/mode/vendor symbol.")
        return MarketSnapshot(as_of, anchor, bars_by_timeframe)


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
        if cadence.total_seconds() <= 0 or first_due_at.tzinfo is None or first_due_at.utcoffset() != timedelta(0):
            raise ValueError("Poll cadence must be positive and first_due_at timezone-aware UTC.")
        schedule = PollSchedule(schedule_id, provider.name, cadence, first_due_at)
        self._schedules[schedule_id] = _RegisteredPoll(schedule, provider, request_factory)
        return schedule

    def schedules(self) -> tuple[PollSchedule, ...]:
        return tuple(item.schedule for item in self._schedules.values())

    def poll_due(self, now: datetime) -> tuple[IngestionReceipt, ...]:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("Polling timestamp must be timezone-aware UTC.")
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
