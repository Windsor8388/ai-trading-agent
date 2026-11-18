"""Economic-event risk gate. It classifies supplied calendar data; it does not fetch news."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .providers.contracts import EconomicEvent


@dataclass(frozen=True, slots=True)
class EventRiskPolicy:
    embargo_before: timedelta = timedelta(minutes=30)
    embargo_after: timedelta = timedelta(minutes=15)
    minimum_importance: str = "HIGH"

    def __post_init__(self) -> None:
        if self.embargo_before.total_seconds() < 0 or self.embargo_after.total_seconds() < 0:
            raise ValueError("Event embargo durations cannot be negative.")
        if self.minimum_importance not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValueError("minimum_importance must be LOW, MEDIUM, HIGH, or CRITICAL.")


@dataclass(frozen=True, slots=True)
class EventRiskAssessment:
    active: bool
    reasons: tuple[str, ...]
    qualifying_events: tuple[EconomicEvent, ...]


class EconomicEventRiskGate:
    """Turns point-in-time calendar records into an explicit entry embargo decision."""

    version = "0.1.0"
    _rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

    def __init__(self, policy: EventRiskPolicy | None = None) -> None:
        self.policy = policy or EventRiskPolicy()

    def assess(self, events: Sequence[EconomicEvent], as_of: datetime) -> EventRiskAssessment:
        if as_of.tzinfo is None:
            raise ValueError("Event-risk assessment time must be timezone-aware.")
        threshold = self._rank[self.policy.minimum_importance]
        qualifying: list[EconomicEvent] = []
        reasons: list[str] = []
        for event in events:
            if event.scheduled_at_utc.tzinfo is None:
                raise ValueError("Economic event timestamps must be timezone-aware.")
            importance = event.importance.upper()
            if self._rank.get(importance, 0) < threshold:
                continue
            start = event.scheduled_at_utc - self.policy.embargo_before
            end = event.scheduled_at_utc + self.policy.embargo_after
            if start <= as_of <= end:
                qualifying.append(event)
                reasons.append(f"{importance} event embargo: {event.event_id} scheduled {event.scheduled_at_utc.isoformat()}.")
        return EventRiskAssessment(bool(qualifying), tuple(reasons), tuple(qualifying))
