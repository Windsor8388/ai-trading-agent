"""Small transparent indicator functions; all calculations use bars available in the snapshot."""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev
from typing import Sequence

from ..models import Candle


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    price: float
    kind: str  # HIGH or LOW


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    if len(candles) < 2:
        return []
    result: list[float] = []
    for prior, current in zip(candles, candles[1:]):
        result.append(max(current.high - current.low, abs(current.high - prior.close), abs(current.low - prior.close)))
    return result


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    values = true_ranges(candles)
    if len(values) < period:
        return None
    return mean(values[-period:])


def confirmed_pivots(candles: Sequence[Candle], wing: int = 2) -> list[Pivot]:
    """Returns pivots confirmable by `wing` later *closed* bars.

    The final `wing` bars are deliberately not candidates; this avoids the common
    error of marking an unconfirmed current swing as though it were known earlier.
    """
    pivots: list[Pivot] = []
    if len(candles) < wing * 2 + 1:
        return pivots
    for i in range(wing, len(candles) - wing):
        window = candles[i - wing : i + wing + 1]
        high = candles[i].high
        low = candles[i].low
        if high == max(bar.high for bar in window) and sum(bar.high == high for bar in window) == 1:
            pivots.append(Pivot(i, high, "HIGH"))
        if low == min(bar.low for bar in window) and sum(bar.low == low for bar in window) == 1:
            pivots.append(Pivot(i, low, "LOW"))
    return pivots


def last_two(pivots: Sequence[Pivot], kind: str) -> tuple[Pivot, Pivot] | None:
    matches = [item for item in pivots if item.kind == kind]
    return (matches[-2], matches[-1]) if len(matches) >= 2 else None


def simple_return_series(candles: Sequence[Candle]) -> list[float]:
    return [(current.close / prior.close) - 1.0 for prior, current in zip(candles, candles[1:]) if prior.close != 0]


def zscore(value: float, values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    deviation = pstdev(values)
    if deviation == 0:
        return 0.0
    return (value - mean(values)) / deviation


def realized_volatility(candles: Sequence[Candle], period: int = 20) -> float | None:
    returns = simple_return_series(candles)
    if len(returns) < period:
        return None
    return pstdev(returns[-period:]) * sqrt(period)


def sma(values: Sequence[float], period: int) -> float | None:
    return mean(values[-period:]) if len(values) >= period else None
