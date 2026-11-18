"""In-memory paper-order simulator with idempotency and conservative OHLC exits.

It never calls a broker, opens a network socket, or handles credentials. A real paper-feed
adapter may pass completed candles to this component only after its data-quality checks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .models import Candle, DecisionAction, InstrumentRiskSpec, TradeSignal


class PaperOrderState(str, Enum):
    ACCEPTED = "ACCEPTED"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class PaperSimulationConfig:
    slippage_bps: float = 0.0
    commission_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.commission_bps < 0:
            raise ValueError("Paper costs cannot be negative.")


@dataclass(frozen=True, slots=True)
class PaperOrder:
    client_order_id: str
    signal: TradeSignal
    state: PaperOrderState
    accepted_at: datetime
    entry_time: datetime | None = None
    entry_price: float | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    net_pnl: float | None = None
    rejection_reason: str | None = None


class PaperExecutionSimulator:
    """A small deterministic state machine for forward-paper testing and reconciliation.

    One `client_order_id` maps to exactly one immutable `PaperOrder`; re-submitting it
    returns the existing object. A hard halt blocks new acceptance and cancels only
    still-pending simulated orders; it never claims to close a real position.
    """

    version = "0.1.0"

    def __init__(self, instrument_spec: InstrumentRiskSpec, config: PaperSimulationConfig | None = None) -> None:
        self.instrument_spec = instrument_spec
        self.config = config or PaperSimulationConfig()
        self._orders: dict[str, PaperOrder] = {}
        self._halted = False

    def orders(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders.values())

    def submit(self, signal: TradeSignal, client_order_id: str, accepted_at: datetime) -> PaperOrder:
        if not client_order_id or client_order_id.strip() != client_order_id:
            raise ValueError("client_order_id must be a non-empty trimmed string.")
        if accepted_at.tzinfo is None:
            raise ValueError("accepted_at must be timezone-aware.")
        if client_order_id in self._orders:
            return self._orders[client_order_id]
        if self._halted:
            order = PaperOrder(client_order_id, signal, PaperOrderState.REJECTED, accepted_at, rejection_reason="Paper kill switch is active.")
        elif signal.status != "PAPER_CANDIDATE" or signal.action not in (DecisionAction.BUY, DecisionAction.SELL):
            order = PaperOrder(client_order_id, signal, PaperOrderState.REJECTED, accepted_at, rejection_reason="Signal is not an approved paper candidate.")
        elif signal.created_at.tzinfo is None or accepted_at < signal.created_at:
            order = PaperOrder(client_order_id, signal, PaperOrderState.REJECTED, accepted_at, rejection_reason="Signal creation time is invalid or later than paper acceptance.")
        elif signal.instrument != self.instrument_spec.symbol or signal.levels is None or not signal.risk.approved or not signal.risk.quantity:
            order = PaperOrder(client_order_id, signal, PaperOrderState.REJECTED, accepted_at, rejection_reason="Signal/spec/risk contract is incomplete or mismatched.")
        else:
            quantity = signal.risk.quantity
            steps = quantity / self.instrument_spec.quantity_step
            if quantity < self.instrument_spec.minimum_quantity or abs(steps - round(steps)) > 1e-9:
                order = PaperOrder(client_order_id, signal, PaperOrderState.REJECTED, accepted_at, rejection_reason="Signal quantity violates paper instrument minimum/step.")
            else:
                order = PaperOrder(client_order_id, signal, PaperOrderState.ACCEPTED, accepted_at)
        self._orders[client_order_id] = order
        return order

    def halt(self) -> tuple[PaperOrder, ...]:
        self._halted = True
        for identifier, order in tuple(self._orders.items()):
            if order.state == PaperOrderState.ACCEPTED:
                self._orders[identifier] = replace(order, state=PaperOrderState.CANCELLED, rejection_reason="Paper kill switch cancelled pending order.")
        return self.orders()

    def _entry_fill(self, action: DecisionAction, price: float) -> float:
        ratio = self.config.slippage_bps / 10_000
        return price * (1 + ratio) if action == DecisionAction.BUY else price * (1 - ratio)

    def _exit_fill(self, action: DecisionAction, price: float) -> float:
        ratio = self.config.slippage_bps / 10_000
        return price * (1 - ratio) if action == DecisionAction.BUY else price * (1 + ratio)

    def _commission(self, price: float, quantity: float) -> float:
        return price * quantity * self.instrument_spec.contract_multiplier * self.config.commission_bps / 10_000

    @staticmethod
    def _valid_levels(action: DecisionAction, entry: float, stop: float, target: float) -> bool:
        return (action == DecisionAction.BUY and stop < entry < target) or (action == DecisionAction.SELL and target < entry < stop)

    def _exit_event(self, order: PaperOrder, candle: Candle) -> tuple[float, str] | None:
        assert order.entry_price is not None and order.signal.levels is not None
        levels = order.signal.levels
        if order.signal.action == DecisionAction.BUY:
            stop_hit, target_hit = candle.low <= levels.stop_loss, candle.high >= levels.targets[0]
            if stop_hit:
                return self._exit_fill(order.signal.action, min(levels.stop_loss, candle.open)), "STOP_FIRST_AMBIGUOUS" if target_hit else "STOP_LOSS"
            if target_hit:
                return self._exit_fill(order.signal.action, levels.targets[0]), "TAKE_PROFIT"
        else:
            stop_hit, target_hit = candle.high >= levels.stop_loss, candle.low <= levels.targets[0]
            if stop_hit:
                return self._exit_fill(order.signal.action, max(levels.stop_loss, candle.open)), "STOP_FIRST_AMBIGUOUS" if target_hit else "STOP_LOSS"
            if target_hit:
                return self._exit_fill(order.signal.action, levels.targets[0]), "TAKE_PROFIT"
        return None

    def process_completed_candle(self, candle: Candle) -> tuple[PaperOrder, ...]:
        """Apply one completed candle; accepted signals fill no earlier than their creation time."""
        for identifier, existing in tuple(self._orders.items()):
            order = existing
            if order.state == PaperOrderState.ACCEPTED and candle.open_time >= order.signal.created_at:
                assert order.signal.levels is not None
                entry = self._entry_fill(order.signal.action, candle.open)
                if not self._valid_levels(order.signal.action, entry, order.signal.levels.stop_loss, order.signal.levels.targets[0]):
                    order = replace(order, state=PaperOrderState.REJECTED, rejection_reason="Next-candle fill makes stop/target invalid.")
                else:
                    order = replace(order, state=PaperOrderState.OPEN, entry_time=candle.open_time, entry_price=entry)
            if order.state == PaperOrderState.OPEN:
                event = self._exit_event(order, candle)
                if event:
                    exit_price, reason = event
                    quantity = order.signal.risk.quantity or 0.0
                    direction = 1 if order.signal.action == DecisionAction.BUY else -1
                    gross = (exit_price - (order.entry_price or 0.0)) * direction * quantity * self.instrument_spec.contract_multiplier
                    costs = self._commission(order.entry_price or 0.0, quantity) + self._commission(exit_price, quantity)
                    order = replace(order, state=PaperOrderState.CLOSED, exit_time=candle.close_time, exit_price=exit_price, exit_reason=reason, net_pnl=gross - costs)
            self._orders[identifier] = order
        return self.orders()
