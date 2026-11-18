"""Auditable order lifecycle state machine, intentionally disconnected from any broker.

This module validates *paper* order candidates, enforces idempotency, records local
lifecycle facts, and makes post-disconnect uncertainty explicit. It never sends a
network request and has no LIVE operating mode. A separately reviewed venue adapter
would still need to implement authentication, rate limiting, durable storage, and
position reconciliation before any live-study approval.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Mapping

from ..models import DecisionAction, InstrumentRiskSpec, TradeSignal
from ..research.ledger import ResearchLedger


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
class ExecutionPolicy:
    """Explicit, product-aware paper-order limits.

    A paper lifecycle needs an allowlist and product specification rather than inferring
    lot size from a ticker. The policy is deliberately separate from the strategy and
    risk engines: it can only reject an intent, never approve a failed one.
    """

    instrument_specs: Mapping[str, InstrumentRiskSpec]
    max_order_notional: float | None = None
    max_order_cash_risk: float | None = None

    def __post_init__(self) -> None:
        if not self.instrument_specs:
            raise ValueError("Execution policy needs at least one explicit instrument specification.")
        if self.max_order_notional is not None and self.max_order_notional <= 0:
            raise ValueError("max_order_notional must be positive when configured.")
        if self.max_order_cash_risk is not None and self.max_order_cash_risk <= 0:
            raise ValueError("max_order_cash_risk must be positive when configured.")
        for symbol, spec in self.instrument_specs.items():
            if symbol != spec.symbol:
                raise ValueError("Execution policy instrument-spec keys must match spec symbols.")

    def reject_reason(self, signal: TradeSignal) -> str | None:
        spec = self.instrument_specs.get(signal.instrument)
        if spec is None:
            return "Instrument is not in the execution allowlist."
        levels = signal.levels
        quantity = signal.risk.quantity
        if levels is None or quantity is None or quantity <= 0:
            return "Signal lacks valid levels or a positive approved quantity."
        if levels.entry <= 0 or levels.stop_loss <= 0 or not levels.targets:
            return "Entry, stop, and at least one target must be positive."
        if signal.action == DecisionAction.BUY and not (levels.stop_loss < levels.entry < levels.targets[0]):
            return "BUY levels must satisfy stop < entry < target."
        if signal.action == DecisionAction.SELL and not (levels.targets[0] < levels.entry < levels.stop_loss):
            return "SELL levels must satisfy target < entry < stop."
        steps = quantity / spec.quantity_step
        if quantity < spec.minimum_quantity or abs(steps - round(steps)) > 1e-9:
            return "Quantity violates the explicit product minimum or increment."
        notional = levels.entry * quantity * spec.contract_multiplier
        if self.max_order_notional is not None and notional > self.max_order_notional:
            return "Order notional exceeds the execution policy limit."
        cash_risk = signal.risk.worst_case_cash_risk if signal.risk.worst_case_cash_risk is not None else signal.risk.max_cash_risk
        if self.max_order_cash_risk is not None and (cash_risk is None or cash_risk > self.max_order_cash_risk):
            return "Worst-case cash risk is missing or exceeds the execution policy limit."
        return None


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    at: datetime
    event_type: str
    detail: str


@dataclass(frozen=True, slots=True)
class VenueOrderSnapshot:
    """One trusted venue observation used only to resolve a previously UNKNOWN order."""

    status: OrderStatus
    cumulative_quantity: float | None
    observed_at: datetime
    venue_order_id: str | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("Venue snapshot timestamps must be timezone-aware.")
        if self.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}:
            raise ValueError(f"Venue snapshot cannot resolve an order to {self.status}.")
        if self.cumulative_quantity is not None and self.cumulative_quantity < 0:
            raise ValueError("Venue cumulative quantity cannot be negative.")


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
    """Local paper lifecycle manager with durable-audit integration when configured.

    `audit_ledger` is optional for isolated unit tests and disposable demonstrations.
    A deployed paper workflow should pass a `ResearchLedger` path on durable storage;
    a failed audit append prevents the corresponding local state transition.
    """

    version = "0.2.0"

    _in_flight = {
        OrderStatus.SUBMITTED,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_REQUESTED,
    }

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.ANALYSIS_ONLY,
        *,
        policy: ExecutionPolicy | None = None,
        audit_ledger: ResearchLedger | None = None,
    ) -> None:
        self.mode = mode
        self.policy = policy
        self.audit_ledger = audit_ledger
        self._orders: dict[str, OrderRecord] = {}
        self._halted = False
        self._connected = True

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def connected(self) -> bool:
        return self._connected

    @staticmethod
    def _aware(at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("Lifecycle timestamps must be timezone-aware.")

    def orders(self) -> tuple[OrderRecord, ...]:
        return tuple(self._orders.values())

    def _persist_event(self, order: OrderRecord, event: ExecutionEvent) -> None:
        if self.audit_ledger is None:
            return
        self.audit_ledger.append(
            f"execution.{event.event_type.lower()}",
            {
                "client_order_id": order.client_order_id,
                "instrument": order.signal.instrument,
                "mode": order.mode.value,
                "order_status": order.status.value,
                "filled_quantity": order.filled_quantity,
                "venue_order_id": order.venue_order_id,
                "event_sequence": event.sequence,
                "detail": event.detail,
            },
            recorded_at=event.at,
        )

    def _store(self, order: OrderRecord) -> OrderRecord:
        event = order.events[-1]
        self._persist_event(order, event)
        self._orders[order.client_order_id] = order
        return order

    def _append(self, order: OrderRecord, at: datetime, event_type: str, detail: str, **changes: object) -> OrderRecord:
        self._aware(at)
        event = ExecutionEvent(len(order.events) + 1, at, event_type, detail)
        updated = replace(order, events=(*order.events, event), **changes)
        return self._store(updated)

    def prepare(self, signal: TradeSignal, client_order_id: str, at: datetime) -> OrderRecord:
        """Validate one candidate locally; this method cannot send an external order."""
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
        elif self.policy is None:
            reason = "Paper lifecycle requires an explicit execution policy and product specification."
        elif signal.status != "PAPER_CANDIDATE" or signal.action not in (DecisionAction.BUY, DecisionAction.SELL):
            reason = "Signal is not an approved paper candidate."
        elif signal.created_at.tzinfo is None or signal.created_at > at:
            reason = "Signal timestamp is invalid or later than order preparation."
        elif not signal.risk.approved:
            reason = "Independent risk approval is missing."
        elif self.policy is not None:
            reason = self.policy.reject_reason(signal)
        status = OrderStatus.REJECTED if reason else OrderStatus.VALIDATED
        event = ExecutionEvent(1, at, "PREPARE_REJECTED" if reason else "VALIDATED", reason or "Local validation passed; awaiting simulated submission.")
        record = OrderRecord(client_order_id, signal, self.mode, status, at, rejection_reason=reason, events=(event,))
        return self._store(record)

    def record_submitted(self, client_order_id: str, venue_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if not self._connected:
            raise RuntimeError("Cannot submit while connection health is unknown; reconcile first.")
        if order.status != OrderStatus.VALIDATED:
            raise ValueError(f"Only VALIDATED orders can be submitted, not {order.status}.")
        if not venue_order_id:
            raise ValueError("venue_order_id is required after submission.")
        return self._append(order, at, "SUBMITTED", f"Venue accepted client id as {venue_order_id}.", status=OrderStatus.SUBMITTED, venue_order_id=venue_order_id)

    def record_acknowledged(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status != OrderStatus.SUBMITTED:
            raise ValueError(f"Only SUBMITTED orders can be acknowledged, not {order.status}.")
        return self._append(order, at, "ACKNOWLEDGED", "Venue acknowledgement recorded.", status=OrderStatus.ACKNOWLEDGED)

    def record_fill(self, client_order_id: str, cumulative_quantity: float, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        quantity = order.signal.risk.quantity or 0.0
        if order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
            raise ValueError(f"Cannot record a fill for {order.status}; reconcile UNKNOWN state first.")
        if cumulative_quantity <= order.filled_quantity or cumulative_quantity > quantity:
            raise ValueError("Filled quantity must increase and cannot exceed approved quantity.")
        status = OrderStatus.FILLED if cumulative_quantity == quantity else OrderStatus.PARTIALLY_FILLED
        return self._append(order, at, "FILL", f"Cumulative filled quantity: {cumulative_quantity}.", status=status, filled_quantity=cumulative_quantity)

    def record_rejected(self, client_order_id: str, reason: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if not reason.strip():
            raise ValueError("A venue rejection requires a non-empty reason.")
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED}:
            raise ValueError(f"Cannot reject {order.status} order; reconcile UNKNOWN state first.")
        return self._append(order, at, "VENUE_REJECTED", reason, status=OrderStatus.REJECTED, rejection_reason=reason)

    def request_cancel(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status == OrderStatus.CANCEL_REQUESTED:
            return order
        if order.status not in self._in_flight:
            raise ValueError(f"Cannot request cancel for {order.status} order.")
        return self._append(order, at, "CANCEL_REQUESTED", "Cancellation requires venue confirmation.", status=OrderStatus.CANCEL_REQUESTED)

    def record_cancelled(self, client_order_id: str, at: datetime) -> OrderRecord:
        order = self._get(client_order_id)
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED}:
            raise ValueError(f"Cannot cancel {order.status} order; reconcile UNKNOWN state first.")
        return self._append(order, at, "CANCELLED", "Cancellation confirmed.", status=OrderStatus.CANCELLED)

    def mark_disconnected(self, at: datetime) -> tuple[OrderRecord, ...]:
        """Mark every in-flight order UNKNOWN; never infer cancellation/fill after an outage."""
        self._aware(at)
        self._connected = False
        for _, order in tuple(self._orders.items()):
            if order.status in self._in_flight:
                self._append(order, at, "CONNECTION_LOST", "Venue state is unknown until reconciliation.", status=OrderStatus.UNKNOWN)
        return self.orders()

    @staticmethod
    def _reconciled_fill(order: OrderRecord, snapshot: VenueOrderSnapshot) -> float:
        approved = order.signal.risk.quantity or 0.0
        quantity = snapshot.cumulative_quantity
        if snapshot.status == OrderStatus.FILLED:
            if quantity is None or abs(quantity - approved) > 1e-9:
                raise ValueError("A FILLED reconciliation snapshot must report the exact approved cumulative quantity.")
        elif snapshot.status == OrderStatus.PARTIALLY_FILLED:
            if quantity is None or not order.filled_quantity < quantity < approved:
                raise ValueError("A PARTIALLY_FILLED snapshot must increase quantity without completing it.")
        elif quantity is not None and not order.filled_quantity <= quantity <= approved:
            raise ValueError("Reconciled cumulative quantity is outside approved bounds.")
        return order.filled_quantity if quantity is None else quantity

    def reconcile(self, venue_states: Mapping[str, OrderStatus | VenueOrderSnapshot], at: datetime) -> tuple[OrderRecord, ...]:
        """Resolve UNKNOWN records from a trusted venue snapshot after reconnect.

        Omitted client IDs remain UNKNOWN: absence from a snapshot is not evidence of
        cancellation. Fill-bearing states require `VenueOrderSnapshot` so a terminal
        fill cannot be fabricated without cumulative quantity and observation time.
        """
        self._aware(at)
        for identifier, result in venue_states.items():
            order = self._get(identifier)
            if order.status != OrderStatus.UNKNOWN:
                continue
            if isinstance(result, VenueOrderSnapshot):
                snapshot = result
                if snapshot.observed_at > at:
                    raise ValueError("Venue snapshot observation cannot be later than reconciliation time.")
                if snapshot.venue_order_id and order.venue_order_id and snapshot.venue_order_id != order.venue_order_id:
                    raise ValueError("Venue snapshot order id conflicts with recorded venue order id.")
                status = snapshot.status
                filled_quantity = self._reconciled_fill(order, snapshot)
                reference = f"; source={snapshot.source_reference}" if snapshot.source_reference else ""
                detail = f"Trusted venue snapshot at {snapshot.observed_at.isoformat()} reports {status}; cumulative quantity {filled_quantity}{reference}."
                venue_order_id = snapshot.venue_order_id or order.venue_order_id
            elif isinstance(result, OrderStatus):
                status = result
                if status in {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED}:
                    raise ValueError("Fill-bearing reconciliation needs a VenueOrderSnapshot with cumulative quantity.")
                if status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED, OrderStatus.CANCELLED}:
                    raise ValueError(f"Cannot reconcile UNKNOWN order to {status}.")
                filled_quantity = order.filled_quantity
                detail = f"Trusted venue snapshot reports {status} without an additional fill."
                venue_order_id = order.venue_order_id
            else:
                raise TypeError("Venue reconciliation values must be OrderStatus or VenueOrderSnapshot.")
            self._append(order, at, "RECONCILED", detail, status=status, filled_quantity=filled_quantity, venue_order_id=venue_order_id)
        self._connected = True
        return self.orders()

    def emergency_stop(self, at: datetime, reason: str) -> tuple[OrderRecord, ...]:
        """Block new intents and safely cancel/reconcile local states; no venue call occurs."""
        self._aware(at)
        if not reason.strip():
            raise ValueError("Emergency stop requires a non-empty reason.")
        self._halted = True
        for _, order in tuple(self._orders.items()):
            if order.status == OrderStatus.VALIDATED:
                self._append(order, at, "EMERGENCY_CANCELLED", reason, status=OrderStatus.CANCELLED)
            elif order.status in self._in_flight:
                self._append(order, at, "EMERGENCY_CANCEL_REQUESTED", reason, status=OrderStatus.CANCEL_REQUESTED)
            elif order.status == OrderStatus.UNKNOWN:
                self._append(order, at, "EMERGENCY_RECONCILIATION_REQUIRED", reason)
        return self.orders()

    def _get(self, client_order_id: str) -> OrderRecord:
        try:
            return self._orders[client_order_id]
        except KeyError as exc:
            raise KeyError(f"Unknown client_order_id: {client_order_id}") from exc
