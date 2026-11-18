"""No-look-ahead event-driven backtest core.

The callback sees a snapshot ending at bar t only. A direction emitted at t becomes a
pending instruction and may enter at bar t+1 open. If stop and target are both touched
within one OHLC bar, the engine uses stop-first rather than choosing the optimistic path.
This is a research simulator, not a broker or proof of tradability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Callable, Sequence

from ..data import validate_closed_candles
from ..models import Candle, DataMode, DecisionAction, FeedMetadata, MarketSnapshot, ProposedLevels
from ..research.metrics import PerformanceMetrics, performance_metrics


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float
    quantity: float
    contract_multiplier: float
    periods_per_year: int
    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    force_close_at_end: bool = True
    allow_data_warnings: bool = False

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.quantity <= 0 or self.contract_multiplier <= 0:
            raise ValueError("initial_cash, quantity, and contract_multiplier must be positive.")
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive.")
        if self.commission_bps < 0 or self.slippage_bps < 0:
            raise ValueError("Costs cannot be negative.")


@dataclass(frozen=True, slots=True)
class BacktestInstruction:
    """A signal generated at a closed bar and eligible only at the next bar open."""

    action: DecisionAction
    levels: ProposedLevels
    strategy_id: str
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.action not in (DecisionAction.BUY, DecisionAction.SELL):
            raise ValueError("A backtest instruction must be BUY or SELL.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0, 1].")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    strategy_id: str
    timeframe: str
    action: DecisionAction
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_loss: float
    target: float
    quantity: float
    exit_reason: str
    gross_pnl: float
    costs: float
    net_pnl: float
    return_on_equity: float
    bars_held: int
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    trade_count: int
    net_profit: float
    final_equity: float
    trade_expectancy: float | None
    trade_profit_factor: float | None
    trade_win_rate: float | None
    equity_bar_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: BacktestConfig
    data_quality_warnings: tuple[str, ...]
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    summary: BacktestSummary
    discarded_instructions: tuple[str, ...]


SignalCallback = Callable[[MarketSnapshot], BacktestInstruction | None]


@dataclass(slots=True)
class _Position:
    action: DecisionAction
    strategy_id: str
    signal_time: datetime
    entry_time: datetime
    entry_index: int
    entry_price: float
    stop_loss: float
    target: float
    entry_fee: float
    equity_before_entry: float
    maximum_favorable_excursion: float = 0.0
    maximum_adverse_excursion: float = 0.0


class BacktestEngine:
    version = "0.1.0"

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config

    def _commission(self, price: float) -> float:
        return price * self.config.quantity * self.config.contract_multiplier * self.config.commission_bps / 10_000

    def _entry_fill(self, action: DecisionAction, opening_price: float) -> float:
        ratio = self.config.slippage_bps / 10_000
        return opening_price * (1 + ratio) if action == DecisionAction.BUY else opening_price * (1 - ratio)

    def _exit_fill(self, action: DecisionAction, raw_price: float) -> float:
        ratio = self.config.slippage_bps / 10_000
        return raw_price * (1 - ratio) if action == DecisionAction.BUY else raw_price * (1 + ratio)

    @staticmethod
    def _invalid_levels(action: DecisionAction, entry: float, levels: ProposedLevels) -> str | None:
        if not levels.targets:
            return "missing target"
        if action == DecisionAction.BUY:
            if levels.stop_loss >= entry:
                return "buy stop is not below next-open fill"
            if levels.targets[0] <= entry:
                return "buy target is not above next-open fill"
        else:
            if levels.stop_loss <= entry:
                return "sell stop is not above next-open fill"
            if levels.targets[0] >= entry:
                return "sell target is not below next-open fill"
        return None

    def _intrabar_exit(self, position: _Position, candle: Candle) -> tuple[float, str] | None:
        """Uses conservative stop-first policy when OHLC cannot reveal intra-bar order."""
        if position.action == DecisionAction.BUY:
            stop_hit, target_hit = candle.low <= position.stop_loss, candle.high >= position.target
            if stop_hit:
                raw = min(position.stop_loss, candle.open)  # gap below stop is filled no better than open.
                return self._exit_fill(position.action, raw), "STOP_FIRST_AMBIGUOUS" if target_hit else "STOP_LOSS"
            if target_hit:
                return self._exit_fill(position.action, position.target), "TAKE_PROFIT"
        else:
            stop_hit, target_hit = candle.high >= position.stop_loss, candle.low <= position.target
            if stop_hit:
                raw = max(position.stop_loss, candle.open)  # gap above stop is filled no better than open.
                return self._exit_fill(position.action, raw), "STOP_FIRST_AMBIGUOUS" if target_hit else "STOP_LOSS"
            if target_hit:
                return self._exit_fill(position.action, position.target), "TAKE_PROFIT"
        return None

    def _update_excursions(self, position: _Position, candle: Candle) -> None:
        multiplier = self.config.quantity * self.config.contract_multiplier
        if position.action == DecisionAction.BUY:
            favorable = (candle.high - position.entry_price) * multiplier
            adverse = (candle.low - position.entry_price) * multiplier
        else:
            favorable = (position.entry_price - candle.low) * multiplier
            adverse = (position.entry_price - candle.high) * multiplier
        position.maximum_favorable_excursion = max(position.maximum_favorable_excursion, favorable)
        position.maximum_adverse_excursion = min(position.maximum_adverse_excursion, adverse)

    def _unrealized(self, position: _Position, price: float) -> float:
        direction = 1 if position.action == DecisionAction.BUY else -1
        return (price - position.entry_price) * direction * self.config.quantity * self.config.contract_multiplier

    def run(self, candles: Sequence[Candle], feed: FeedMetadata, timeframe: str, signal_callback: SignalCallback) -> BacktestResult:
        """Replay bars in chronological order. No future bars reach `signal_callback`."""
        if len(candles) < 2:
            raise ValueError("At least two completed candles are required for next-open replay.")
        if feed.mode not in (DataMode.HISTORICAL, DataMode.SYNTHETIC):
            raise ValueError("Backtest accepts only HISTORICAL or SYNTHETIC replay data, never a live feed.")
        quality = validate_closed_candles(candles, candles[-1].close_time)
        if not quality.passed:
            raise ValueError(f"Backtest input rejected: {'; '.join(quality.errors)}")
        if quality.warnings and not self.config.allow_data_warnings:
            raise ValueError(f"Backtest input has unresolved data warnings: {'; '.join(quality.warnings)}")

        cash = self.config.initial_cash
        position: _Position | None = None
        pending: tuple[BacktestInstruction, datetime] | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[EquityPoint] = []
        discarded: list[str] = []

        def close_position(exit_price: float, reason: str, candle: Candle, index: int) -> None:
            nonlocal cash, position
            assert position is not None
            gross = self._unrealized(position, exit_price)
            exit_fee = self._commission(exit_price)
            costs = position.entry_fee + exit_fee
            net = gross - costs
            cash += gross - exit_fee  # Entry fee was debited at entry.
            trades.append(BacktestTrade(
                position.strategy_id, timeframe, position.action, position.signal_time, position.entry_time, candle.close_time,
                position.entry_price, exit_price, position.stop_loss, position.target, self.config.quantity, reason,
                gross, costs, net, net / position.equity_before_entry, index - position.entry_index + 1,
                position.maximum_favorable_excursion, position.maximum_adverse_excursion,
            ))
            position = None

        for index, candle in enumerate(candles):
            if pending is not None:
                instruction, signal_time = pending
                pending = None
                entry_price = self._entry_fill(instruction.action, candle.open)
                invalid = self._invalid_levels(instruction.action, entry_price, instruction.levels)
                if invalid:
                    discarded.append(f"{signal_time.isoformat()} {instruction.strategy_id}: discarded after gap/validation: {invalid}.")
                else:
                    entry_fee = self._commission(entry_price)
                    equity_before_entry = cash
                    cash -= entry_fee
                    position = _Position(
                        instruction.action, instruction.strategy_id, signal_time, candle.open_time, index, entry_price,
                        instruction.levels.stop_loss, instruction.levels.targets[0], entry_fee, equity_before_entry,
                    )

            if position is not None:
                self._update_excursions(position, candle)
                exit_event = self._intrabar_exit(position, candle)
                if exit_event is not None:
                    exit_price, reason = exit_event
                    close_position(exit_price, reason, candle, index)
                elif index == len(candles) - 1 and self.config.force_close_at_end:
                    close_position(self._exit_fill(position.action, candle.close), "END_OF_TEST_FORCED_CLOSE", candle, index)

            marked_equity = cash + (self._unrealized(position, candle.close) if position is not None else 0.0)
            equity_curve.append(EquityPoint(candle.close_time, marked_equity))

            # Signal is generated only after current bar closes and can never enter this bar.
            if position is None and index < len(candles) - 1:
                snapshot = MarketSnapshot(candle.close_time, feed, {timeframe: tuple(candles[: index + 1])})
                instruction = signal_callback(snapshot)
                if instruction is not None:
                    pending = (instruction, candle.close_time)

        bar_returns = [current.equity / prior.equity - 1 for prior, current in zip(equity_curve, equity_curve[1:]) if prior.equity != 0]
        equity_metrics = performance_metrics(bar_returns, self.config.periods_per_year)
        net_pnls = [trade.net_pnl for trade in trades]
        gains = sum(value for value in net_pnls if value > 0)
        losses = sum(value for value in net_pnls if value < 0)
        summary = BacktestSummary(
            trade_count=len(trades), net_profit=cash - self.config.initial_cash, final_equity=cash,
            trade_expectancy=mean(net_pnls) if net_pnls else None,
            trade_profit_factor=gains / abs(losses) if losses else None,
            trade_win_rate=sum(value > 0 for value in net_pnls) / len(net_pnls) if net_pnls else None,
            equity_bar_metrics=equity_metrics,
        )
        return BacktestResult(self.config, tuple(quality.warnings), tuple(trades), tuple(equity_curve), summary, tuple(discarded))
