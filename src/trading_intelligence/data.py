"""Data validation and no-look-ahead utilities. Provider adapters belong outside this module."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .models import Candle


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    passed: bool
    checked_bars: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_closed_candles(candles: Sequence[Candle], as_of: datetime) -> DataQualityReport:
    """Reject duplicate, out-of-order, future, or malformed bars before analysis.

    It intentionally does not silently sort or fill gaps: doing so can hide a vendor
    defect or create synthetic information. Gap policy belongs to the provider layer.
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen = set()
    prior_open = None
    prior_close = None
    for index, candle in enumerate(candles):
        if candle.open_time in seen:
            errors.append(f"duplicate open_time at index {index}: {candle.open_time.isoformat()}")
        seen.add(candle.open_time)
        if prior_open is not None and candle.open_time <= prior_open:
            errors.append(f"non-increasing open_time at index {index}")
        if candle.close_time > as_of:
            errors.append(f"unclosed/future bar at index {index}")
        if prior_close is not None and candle.open_time > prior_close:
            warnings.append(f"time gap before index {index}; provider policy must classify it")
        prior_open = candle.open_time
        prior_close = candle.close_time
    if not candles:
        errors.append("no candles supplied")
    return DataQualityReport(not errors, len(candles), tuple(errors), tuple(warnings))


def completed_only(candles: Sequence[Candle], as_of: datetime) -> tuple[Candle, ...]:
    """Returns only bars closed at `as_of`; does not mutate or fabricate bars."""
    return tuple(candle for candle in candles if candle.close_time <= as_of)


def timeframe_seconds(timeframe: str) -> int:
    values = {
        "MN": 30 * 24 * 3600, "W1": 7 * 24 * 3600, "D1": 24 * 3600,
        "H4": 4 * 3600, "H1": 3600, "M30": 1800, "M15": 900,
        "M5": 300, "M1": 60,
    }
    try:
        return values[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported canonical timeframe: {timeframe}") from exc


def resample_intraday_bars(candles: Sequence[Candle], source_timeframe: str, target_timeframe: str) -> tuple[Candle, ...]:
    """Aggregate complete, contiguous intraday bars without filling gaps.

    This deliberately supports only minute/hour timeframes aligned to UTC epoch. Daily,
    weekly, and monthly candles require an explicit venue/session alignment policy and
    must be built by the provider adapter rather than guessed here.
    """
    source_seconds = timeframe_seconds(source_timeframe)
    target_seconds = timeframe_seconds(target_timeframe)
    if source_timeframe in {"D1", "W1", "MN"} or target_timeframe in {"D1", "W1", "MN"}:
        raise ValueError("Daily/weekly/monthly resampling requires a provider session-alignment policy.")
    if target_seconds <= source_seconds or target_seconds % source_seconds:
        raise ValueError("Target timeframe must be an integer multiple of a smaller source timeframe.")
    if not candles:
        return ()
    quality = validate_closed_candles(candles, candles[-1].close_time)
    if not quality.passed:
        raise ValueError(f"Cannot resample invalid bars: {'; '.join(quality.errors)}")
    expected = target_seconds // source_seconds
    grouped: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = int(candle.open_time.timestamp()) // target_seconds
        grouped.setdefault(bucket, []).append(candle)
    result: list[Candle] = []
    for bucket in sorted(grouped):
        group = grouped[bucket]
        # Missing source bars invalidate this aggregate rather than creating a synthetic candle.
        contiguous = all(current.open_time == prior.close_time for prior, current in zip(group, group[1:]))
        aligned = int(group[0].open_time.timestamp()) % target_seconds == 0
        if len(group) != expected or not contiguous or not aligned:
            continue
        volumes = [bar.volume for bar in group]
        volume = sum(value for value in volumes if value is not None) if all(value is not None for value in volumes) else None
        result.append(Candle(group[0].open_time, group[-1].close_time, group[0].open, max(bar.high for bar in group), min(bar.low for bar in group), group[-1].close, volume))
    return tuple(result)
