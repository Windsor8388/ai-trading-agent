"""Transparent metrics and temporal splitting for later backtest/paper-trading adapters."""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from random import Random
from statistics import mean, pstdev
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    observations: int
    total_return: float
    expectancy: float | None
    profit_factor: float | None
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    win_rate: float | None


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for result in returns:
        equity *= 1 + result
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def performance_metrics(returns: Sequence[float], periods_per_year: int) -> PerformanceMetrics:
    """Metrics for a supplied, chronologically ordered series of realised net returns.

    Costs, fills, financing, and liquidation must already be included upstream.
    `None` means unavailable rather than an invented favourable value.
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if not returns:
        return PerformanceMetrics(0, 0.0, None, None, 0.0, None, None, None)
    total = 1.0
    for item in returns:
        total *= 1 + item
    wins = [item for item in returns if item > 0]
    losses = [item for item in returns if item < 0]
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else None
    deviation = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = (mean(returns) / deviation * sqrt(periods_per_year)) if deviation else None
    downside = [min(0.0, item) for item in returns]
    downside_deviation = sqrt(mean(item * item for item in downside))
    sortino = (mean(returns) / downside_deviation * sqrt(periods_per_year)) if downside_deviation else None
    return PerformanceMetrics(
        observations=len(returns), total_return=total - 1, expectancy=mean(returns), profit_factor=profit_factor,
        max_drawdown=_max_drawdown(returns), sharpe=sharpe, sortino=sortino,
        win_rate=len(wins) / len(returns),
    )


def walk_forward_splits(length: int, train_size: int, test_size: int, step: int | None = None) -> tuple[tuple[range, range], ...]:
    """Expanding chronological folds; train indices always strictly precede test indices."""
    if min(length, train_size, test_size) <= 0:
        raise ValueError("length, train_size, and test_size must be positive")
    step = step or test_size
    if step <= 0:
        raise ValueError("step must be positive")
    folds = []
    train_start = 0
    train_end = train_size
    while train_end + test_size <= length:
        train = range(train_start, train_end)
        test = range(train_end, train_end + test_size)
        if train.stop > test.start:
            raise AssertionError("Temporal leakage: training overlaps test")
        folds.append((train, test))
        train_end += step
    return tuple(folds)


def monte_carlo_bootstrap(returns: Sequence[float], iterations: int = 1_000, seed: int = 7) -> tuple[float, ...]:
    """Bootstrap terminal returns for uncertainty exploration; preserves a recorded seed."""
    if not returns:
        return ()
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    generator = Random(seed)
    outcomes = []
    for _ in range(iterations):
        equity = 1.0
        for _ in returns:
            equity *= 1 + returns[generator.randrange(len(returns))]
        outcomes.append(equity - 1)
    return tuple(outcomes)
