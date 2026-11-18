"""Auditable order lifecycle state machine, intentionally disconnected from any broker.

The manager validates candidates, enforces idempotency, records acknowledgements/rejections,
and makes uncertainty after a disconnect explicit. It never sends a network request and
cannot enable live trading; a future venue adapter must be separately reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Mapping

from ..models import DecisionAction, TradeSignal


class ExecutionMode(str, Enum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    PAPER = "PAPER"
    LIVE_DISABLED = "LIVE_DISABLED"


class OrderStatus(str, Enum):
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    at: datetime
    event_type: str
    detail: str


@dataclass(frozen=True, slots=True)
class OrderRecord:
    client_order_id: str
    signal: TradeSignal
    mode: ExecutionMode
    status: OrderStatus
    created_at: datetime
    venue_order_id: str | None = None
    filled_quantity: float = 0.0
    rejection_reason: str | None = None
    events: tuple[ExecutionEvent, ...] = ()


class OrderLifecycleManager:
    """Local lifecycle manager for analysis/paper workflows and future venue reconciliation."""

    version = "0.1.0"

    _open_for_reconciliation = {OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED}

    def __init__(self, mode: ExecutionMode = ExecutionMode.ANALYSIS_ONLY) -> None:
        self.mode = mode
        self._orders: dict[str, OrderRecord] = {}
        self._halted = False
        self._connected = True

    @staticmethod
    def _aware(at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("Lifecycle timestamps must be timezone-aware.")

    def orders(self) -> tuple[OrderRecord, ...]:
        return tuple(self._orders.values())

    def _append(self, order: OrderRecord, at: datetime, event_type: str, detail: str, **changes: object) -> OrderRecord:
        self._aware(at)
        event = ExecutionEvent(len(order.events) + 1, at, event_type, detail)
        updated = replace(order, events=(*order.events, event), **changes)
        self._orders[order.client_order_id] = updated
        return updated

    def prepare(self, signal: TradeSignal, client_order_id: str, at: datetime) -> OrderRecord:
        """Validate a candidate locally; no external order is sent from this method."""
        self._aware(at)
        if not client_order_id or client_order_id.strip() != client_order_id:
            raise ValueError("client_order_id must be a non-empty trimmed string.")
        if client_order_id in self._orders:
            return self._orders[client_order_id]
        reason: str | None = None
        if self._halted:
            reason = "Emergency stop is active."
        elif self.mode == ExecutionMode.LIVE_DISABLED:
            reason = "Live execution is intentionally disabled; no venue adapter is installed."
        elif self.mode != ExecutionMode.PAPER:
            reason = "Order lifecycle is in analysis-only mode."
        elif signal.status != "PAPER_CANDIDATE" or signal.action not in (DecisionAction.BUY, DecisionAction.SELL):
            reason = "Signal is not an approved paper candidate."
        elif signal.levels is None or not signal.risk.approved or not signal.risk.quantity:
            reason = "Signal lacks valid levels or approved risk sizing."
        elif signal.created_at.tzinfo is None or signal.created_at > at:
            reason = "Signal timestamp is invalid or later than order preparation."
        status = OrderStatus.REJECTED if reason else OrderStatus.VALIDATED
        event = ExecutionEvent(1, at, "PREPARE_REJECTED" if reason else "VALIDATED", reason or "Local validation passed; awaiting simulated/venue submission.")
        record = OrderRecord(client_order_id, signal, self.mode, status, at, rejection_reason=reason, events=(event,))
        self._orders[client_order_id] = record
        return record

    def record_submitted(self, client_order_id: str, venue_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status != OrderStatus.VALIDATED:
            raise ValueError(f"Only VALIDATED orders can be submitted, not {order.status}.")
        if not venue_order_id:
            raise ValueError("venue_order_id is required after submission.")
        return self._append(order, at, "SUBMITTED", f"Venue accepted client id as {venue_order_id}.", status=OrderStatus.SUBMITTED, venue_order_id=venue_order_id)

    def record_acknowledged(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status not in {OrderStatus.SUBMITTED, OrderStatus.UNKNOWN}:
            raise ValueError(f"Cannot acknowledge {order.status} order.")
        return self._append(order, at, "ACKNOWLEDGED", "Venue acknowledgement recorded.", status=OrderStatus.ACKNOWLEDGED)

    def record_fill(self, client_order_id: str, cumulative_quantity: float, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        quantity = order.signal.risk.quantity or 0.0
        if order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.UNKNOWN}:
            raise ValueError(f"Cannot fill {order.status} order.")
        if cumulative_quantity <= order.filled_quantity or cumulative_quantity > quantity:
            raise ValueError("Filled quantity must increase and cannot exceed approved quantity.")
        status = OrderStatus.FILLED if cumulative_quantity == quantity else OrderStatus.PARTIALLY_FILLED
        return self._append(order, at, "FILL", f"Cumulative filled quantity: {cumulative_quantity}.", status=status, filled_quantity=cumulative_quantity)

    def record_rejected(self, client_order_id: str, reason: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTED, OrderStatus.UNKNOWN, OrderStatus.ACKNOWLEDGED}:
            raise ValueError(f"Cannot reject {order.status} order.")
        return self._append(order, at, "VENUE_REJECTED", reason, status=OrderStatus.REJECTED, rejection_reason=reason)

    def request_cancel(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status not in self._open_for_reconciliation:
            raise ValueError(f"Cannot request cancel for {order.status} order.")
        return self._append(order, at, "CANCEL_REQUESTED", "Cancellation requires venue confirmation.", status=OrderStatus.CANCEL_REQUESTED)

    def record_cancelled(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED, OrderStatus.UNKNOWN}:
            raise ValueError(f"Cannot cancel {order.status} order.")
        return self._append(order, at, "CANCELLED", "Cancellation confirmed.", status=OrderStatus.CANCELLED)

    def mark_disconnected(self, at: datetime) -> tuple[OrderRecord, ...]:
        """Mark in-flight orders UNKNOWN; never assume cancellation/fill after an outage."""
        self._connected = False
        for identifier, order in tuple(self._orders.items()):
            if order.status in self._open_for_reconciliation:
                self._append(order, at, "CONNECTION_LOST", "Venue state is unknown until reconciliation.", status=OrderStatus.UNKNOWN)
        return self.orders()

    def reconcile(self, venue_states: Mapping[str, OrderStatus], at: datetime) -> tuple[OrderRecord, ...]:
        """Resolve UNKNOWN orders from a trusted venue snapshot after reconnect.

        Any unknown client id omitted by the venue remains UNKNOWN: absence is not proof
        of cancellation. A production adapter must retain the source response hash.
        """
        self._connected = True
        for identifier, state in venue_states.items():
            order = self._get(identifier)
            if order.status != OrderStatus.UNKNOWN:
                continue
            if state not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}:
                raise ValueError(f"Cannot reconcile UNKNOWN order to {state}.")
            self._append(order, at, "RECONCILED", f"Venue snapshot reports {state}.", status=state)
        return self.orders()

    def emergency_stop(self, at: datetime, reason: str) -> tuple[OrderRecord, ...]:
        """Block new requests and cancel only local pending orders; no real venue call occurs."""
        self._halted = True
        for identifier, order in tuple(self._orders.items()):
            if order.status == OrderStatus.VALIDATED:
                self._append(order, at, "EMERGENCY_CANCELLED", reason, status=OrderStatus.CANCELLED)
            elif order.status in self._open_for_reconciliation:
                self._append(order, at, "EMERGENCY_CANCEL_REQUESTED", reason, status=OrderStatus.CANCEL_REQUESTED)
        return self.orders()

    def _get(self, client_order_id: str) -> OrderRecord:
        try:
            return self._orders[client_order_id]
        except KeyError as exc:
            raise KeyError(f"Unknown client_order_id: {client_order_id}") from exc
