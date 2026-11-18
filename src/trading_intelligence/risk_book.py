"""Deterministic portfolio-risk state for the independent risk-management engine.

It is deliberately in-memory and append-only by caller discipline. Production persistence
must put the same events in durable storage and reconcile them with the venue; this module
never contacts a broker or invents mark-to-market prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import PortfolioState


@dataclass(frozen=True, slots=True)
class OpenRiskPosition:
    position_id: str
    instrument: str
    correlation_group: str
    gross_notional: float
    opened_at: datetime

    def __post_init__(self) -> None:
        if not self.position_id or not self.instrument or not self.correlation_group:
            raise ValueError("Risk positions require non-empty identity, instrument, and correlation group.")
        if self.opened_at.tzinfo is None or self.gross_notional < 0:
            raise ValueError("Risk positions require an aware timestamp and non-negative notional.")


@dataclass(frozen=True, slots=True)
class RealizedRiskEvent:
    position_id: str
    closed_at: datetime
    realized_pnl: float

    def __post_init__(self) -> None:
        if self.closed_at.tzinfo is None:
            raise ValueError("Realized risk events require timezone-aware timestamps.")


class PortfolioRiskBook:
    """Tracks realized daily/weekly loss, drawdown, exposure, and correlation concentration.

    Daily and weekly loss are defined here as negative *net realised P&L* within UTC
    calendar day/week (Monday start). This convention is explicit so callers do not
    accidentally mix it with gross losses or unrealised P&L.
    """

    version = "0.1.0"

    def __init__(self, initial_equity: float) -> None:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive.")
        self._equity = initial_equity
        self._high_water_mark = initial_equity
        self._positions: dict[str, OpenRiskPosition] = {}
        self._realized: list[RealizedRiskEvent] = []
        self._consecutive_losses = 0

    @property
    def equity(self) -> float:
        return self._equity

    def open_position(self, position: OpenRiskPosition) -> None:
        if position.position_id in self._positions:
            raise ValueError(f"Duplicate position_id: {position.position_id}")
        self._positions[position.position_id] = position

    def close_position(self, position_id: str, realized_pnl: float, closed_at: datetime) -> RealizedRiskEvent:
        if closed_at.tzinfo is None:
            raise ValueError("closed_at must be timezone-aware.")
        if position_id not in self._positions:
            raise KeyError(f"Unknown open position: {position_id}")
        del self._positions[position_id]
        event = RealizedRiskEvent(position_id, closed_at, realized_pnl)
        self._realized.append(event)
        self._equity += realized_pnl
        self._high_water_mark = max(self._high_water_mark, self._equity)
        self._consecutive_losses = self._consecutive_losses + 1 if realized_pnl < 0 else 0
        return event

    def positions(self) -> tuple[OpenRiskPosition, ...]:
        return tuple(self._positions.values())

    def realized_events(self) -> tuple[RealizedRiskEvent, ...]:
        return tuple(self._realized)

    def _realized_pnl_since(self, as_of: datetime, predicate) -> float:
        return sum(event.realized_pnl for event in self._realized if event.closed_at <= as_of and predicate(event.closed_at))

    def portfolio_state(
        self,
        as_of: datetime,
        *,
        candidate_correlation_group: str | None = None,
        event_risk_active: bool = False,
        gap_risk_active: bool = False,
    ) -> PortfolioState:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware.")
        as_of_utc = as_of.astimezone(timezone.utc).date()
        week = as_of_utc.isocalendar()[:2]
        daily_net = self._realized_pnl_since(as_of, lambda timestamp: timestamp.astimezone(timezone.utc).date() == as_of_utc)
        weekly_net = self._realized_pnl_since(as_of, lambda timestamp: timestamp.astimezone(timezone.utc).date().isocalendar()[:2] == week)
        group_counts: dict[str, int] = {}
        for position in self._positions.values():
            group_counts[position.correlation_group] = group_counts.get(position.correlation_group, 0) + 1
        correlated_count = group_counts.get(candidate_correlation_group, 0) if candidate_correlation_group else max(group_counts.values(), default=0)
        gross_exposure = sum(position.gross_notional for position in self._positions.values()) / self._equity if self._equity else float("inf")
        drawdown = max(0.0, 1 - self._equity / self._high_water_mark)
        return PortfolioState(
            equity=self._equity,
            realized_daily_loss=max(0.0, -daily_net),
            realized_weekly_loss=max(0.0, -weekly_net),
            drawdown_fraction=drawdown,
            open_positions=len(self._positions),
            correlated_open_positions=correlated_count,
            consecutive_losses=self._consecutive_losses,
            gross_exposure_fraction=gross_exposure,
            event_risk_active=event_risk_active,
            gap_risk_active=gap_risk_active,
        )
