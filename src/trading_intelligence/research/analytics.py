"""Performance attribution and drawdown analysis for completed backtest/paper trades.

The analyzer does not manufacture an annualized Sharpe from irregular trade timestamps.
Pass `trade_periods_per_year` only when that convention is documented; otherwise Sharpe
and Sortino remain None for grouped trade returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Mapping, Protocol, Sequence


class TradeLike(Protocol):
    strategy_id: str
    timeframe: str
    entry_time: datetime
    net_pnl: float
    return_on_equity: float
    bars_held: int


class EquityPointLike(Protocol):
    time: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class SessionWindow:
    name: str
    start_hour_utc: int
    end_hour_utc: int

    def __post_init__(self) -> None:
        if not self.name or not 0 <= self.start_hour_utc <= 23 or not 1 <= self.end_hour_utc <= 24 or self.start_hour_utc == self.end_hour_utc:
            raise ValueError("Session requires a name and distinct UTC start/end hours.")

    def contains(self, timestamp: datetime) -> bool:
        hour = timestamp.astimezone(timezone.utc).hour
        if self.start_hour_utc < self.end_hour_utc:
            return self.start_hour_utc <= hour < self.end_hour_utc
        return hour >= self.start_hour_utc or hour < self.end_hour_utc


# Generic UTC labels only. They are not exchange calendars and must be overridden for
# venue/DST-sensitive research.
DEFAULT_UTC_SESSIONS: tuple[SessionWindow, ...] = (
    SessionWindow("ASIA_UTC", 0, 8),
    SessionWindow("LONDON_UTC", 8, 13),
    SessionWindow("NEW_YORK_UTC", 13, 21),
    SessionWindow("OFF_SESSION_UTC", 21, 24),
)


@dataclass(frozen=True, slots=True)
class DrawdownEpisode:
    peak_time: datetime
    trough_time: datetime
    recovered_time: datetime | None
    peak_equity: float
    trough_equity: float
    max_drawdown_fraction: float
    duration_points: int


@dataclass(frozen=True, slots=True)
class TradePerformance:
    trade_count: int
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    expectancy: float | None
    win_rate: float | None
    compound_return: float | None
    sharpe: float | None
    sortino: float | None
    average_bars_held: float | None


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    overall: TradePerformance
    by_strategy: Mapping[str, TradePerformance]
    by_timeframe: Mapping[str, TradePerformance]
    by_session: Mapping[str, TradePerformance]
    drawdowns: tuple[DrawdownEpisode, ...]


def classify_session(timestamp: datetime, sessions: Sequence[SessionWindow] = DEFAULT_UTC_SESSIONS) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("Session attribution requires a timezone-aware timestamp.")
    for session in sessions:
        if session.contains(timestamp):
            return session.name
    return "UNCLASSIFIED"


def drawdown_episodes(equity_curve: Sequence[EquityPointLike]) -> tuple[DrawdownEpisode, ...]:
    """Extract peak-to-trough-to-recovery episodes from chronologically ordered equity."""
    if not equity_curve:
        return ()
    previous_time: datetime | None = None
    for point in equity_curve:
        if point.time.tzinfo is None or (previous_time is not None and point.time < previous_time):
            raise ValueError("Equity curve must be ordered with timezone-aware timestamps.")
        previous_time = point.time
    peak = equity_curve[0]
    episode_start_index: int | None = None
    trough = peak
    episodes: list[DrawdownEpisode] = []
    for index, point in enumerate(equity_curve[1:], start=1):
        if point.equity >= peak.equity:
            if episode_start_index is not None:
                episodes.append(DrawdownEpisode(
                    peak.time, trough.time, point.time, peak.equity, trough.equity,
                    trough.equity / peak.equity - 1 if peak.equity else 0.0,
                    index - episode_start_index + 1,
                ))
                episode_start_index = None
            peak = point
            trough = point
            continue
        if episode_start_index is None:
            episode_start_index = index - 1
            trough = point
        elif point.equity < trough.equity:
            trough = point
    if episode_start_index is not None:
        final = equity_curve[-1]
        episodes.append(DrawdownEpisode(
            peak.time, trough.time, None, peak.equity, trough.equity,
            trough.equity / peak.equity - 1 if peak.equity else 0.0,
            len(equity_curve) - episode_start_index,
        ))
    return tuple(episodes)


class PerformanceAnalyzer:
    version = "0.1.0"

    def __init__(self, *, sessions: Sequence[SessionWindow] = DEFAULT_UTC_SESSIONS, trade_periods_per_year: int | None = None) -> None:
        if not sessions:
            raise ValueError("At least one session window is required.")
        if trade_periods_per_year is not None and trade_periods_per_year <= 0:
            raise ValueError("trade_periods_per_year must be positive when supplied.")
        self.sessions = tuple(sessions)
        self.trade_periods_per_year = trade_periods_per_year

    def _summarize(self, trades: Sequence[TradeLike]) -> TradePerformance:
        if not trades:
            return TradePerformance(0, 0.0, 0.0, 0.0, None, None, None, None, None, None, None)
        net_pnls = [trade.net_pnl for trade in trades]
        returns = [trade.return_on_equity for trade in trades]
        gross_profit = sum(value for value in net_pnls if value > 0)
        gross_loss = sum(value for value in net_pnls if value < 0)
        compound = 1.0
        for value in returns:
            compound *= 1 + value
        deviation = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe = mean(returns) / deviation * sqrt(self.trade_periods_per_year) if deviation and self.trade_periods_per_year else None
        downside = [min(0.0, value) for value in returns]
        downside_deviation = sqrt(mean(value * value for value in downside))
        sortino = mean(returns) / downside_deviation * sqrt(self.trade_periods_per_year) if downside_deviation and self.trade_periods_per_year else None
        return TradePerformance(
            trade_count=len(trades), net_pnl=sum(net_pnls), gross_profit=gross_profit, gross_loss=gross_loss,
            profit_factor=gross_profit / abs(gross_loss) if gross_loss else None,
            expectancy=mean(net_pnls), win_rate=sum(value > 0 for value in net_pnls) / len(net_pnls),
            compound_return=compound - 1, sharpe=sharpe, sortino=sortino,
            average_bars_held=mean(trade.bars_held for trade in trades),
        )

    def analyze(self, trades: Sequence[TradeLike], equity_curve: Sequence[EquityPointLike]) -> PerformanceReport:
        by_strategy: dict[str, list[TradeLike]] = {}
        by_timeframe: dict[str, list[TradeLike]] = {}
        by_session: dict[str, list[TradeLike]] = {}
        for trade in trades:
            by_strategy.setdefault(trade.strategy_id, []).append(trade)
            by_timeframe.setdefault(trade.timeframe, []).append(trade)
            by_session.setdefault(classify_session(trade.entry_time, self.sessions), []).append(trade)
        return PerformanceReport(
            overall=self._summarize(trades),
            by_strategy={key: self._summarize(value) for key, value in sorted(by_strategy.items())},
            by_timeframe={key: self._summarize(value) for key, value in sorted(by_timeframe.items())},
            by_session={key: self._summarize(value) for key, value in sorted(by_session.items())},
            drawdowns=drawdown_episodes(equity_curve),
        )
