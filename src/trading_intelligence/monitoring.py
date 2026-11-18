"""Local observability primitives for data freshness and auditable operational incidents.

This module emits state only; alert delivery (email, Slack, PagerDuty, etc.) belongs to a
separate integration and must not make trading decisions or bypass risk controls.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class FeedKey:
    provider: str
    instrument: str
    timeframe: str


@dataclass(frozen=True, slots=True)
class FeedHealth:
    key: FeedKey
    last_heartbeat_at: datetime
    age_seconds: float
    status: HealthStatus


@dataclass(frozen=True, slots=True)
class OperationalIncident:
    incident_id: str
    category: str
    severity: HealthStatus
    opened_at: datetime
    message: str
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    as_of: datetime
    overall: HealthStatus
    feeds: tuple[FeedHealth, ...]
    open_incidents: tuple[OperationalIncident, ...]


class OperationalMonitor:
    """In-memory monitor suitable for tests and a local PoC; persist events in production."""

    version = "0.1.0"

    def __init__(self) -> None:
        self._heartbeats: dict[FeedKey, datetime] = {}
        self._incidents: dict[str, OperationalIncident] = {}

    @staticmethod
    def _aware(timestamp: datetime, field: str) -> None:
        if timestamp.tzinfo is None:
            raise ValueError(f"{field} must be timezone-aware.")

    def record_heartbeat(self, key: FeedKey, at: datetime) -> None:
        self._aware(at, "heartbeat timestamp")
        if not key.provider or not key.instrument or not key.timeframe:
            raise ValueError("Feed key fields must be non-empty.")
        previous = self._heartbeats.get(key)
        if previous is not None and at < previous:
            raise ValueError("Heartbeat timestamps cannot move backward.")
        self._heartbeats[key] = at

    def open_incident(self, incident: OperationalIncident) -> None:
        self._aware(incident.opened_at, "incident opened_at")
        if incident.incident_id in self._incidents:
            raise ValueError(f"Incident already exists: {incident.incident_id}")
        self._incidents[incident.incident_id] = incident

    def resolve_incident(self, incident_id: str, resolved_at: datetime) -> OperationalIncident:
        self._aware(resolved_at, "incident resolved_at")
        try:
            incident = self._incidents[incident_id]
        except KeyError as exc:
            raise KeyError(f"Unknown incident: {incident_id}") from exc
        if incident.resolved_at is not None:
            return incident
        if resolved_at < incident.opened_at:
            raise ValueError("Incident cannot resolve before it opens.")
        resolved = replace(incident, resolved_at=resolved_at)
        self._incidents[incident_id] = resolved
        return resolved

    def snapshot(self, as_of: datetime, *, stale_after: timedelta) -> MonitoringSnapshot:
        self._aware(as_of, "as_of")
        if stale_after.total_seconds() <= 0:
            raise ValueError("stale_after must be positive.")
        feeds = []
        for key, heartbeat in self._heartbeats.items():
            age = (as_of - heartbeat).total_seconds()
            status = HealthStatus.HEALTHY if age <= stale_after.total_seconds() else HealthStatus.CRITICAL
            feeds.append(FeedHealth(key, heartbeat, age, status))
        open_incidents = tuple(sorted((item for item in self._incidents.values() if item.resolved_at is None), key=lambda item: item.opened_at))
        critical = any(item.status == HealthStatus.CRITICAL for item in feeds) or any(item.severity == HealthStatus.CRITICAL for item in open_incidents)
        degraded = any(item.severity == HealthStatus.DEGRADED for item in open_incidents)
        overall = HealthStatus.CRITICAL if critical else HealthStatus.DEGRADED if degraded else HealthStatus.HEALTHY
        return MonitoringSnapshot(as_of, overall, tuple(sorted(feeds, key=lambda item: (item.key.provider, item.key.instrument, item.key.timeframe))), open_incidents)
