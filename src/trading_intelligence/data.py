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
