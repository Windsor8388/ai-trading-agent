"""Provider contracts; implementations remain read-only until explicitly approved."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from ..models import Candle, FeedMetadata


@dataclass(frozen=True, slots=True)
class BarRequest:
    instrument: str
    timeframe: str
    start_utc: datetime
    end_utc: datetime
    price_component: str

    def __post_init__(self) -> None:
        if not self.instrument or not self.timeframe or not self.price_component:
            raise ValueError("Bar requests require instrument, timeframe, and price component.")
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None or self.end_utc <= self.start_utc:
            raise ValueError("Bar request timestamps must be aware and increasing.")


@dataclass(frozen=True, slots=True)
class MarketDataResponse:
    candles: Sequence[Candle]
    feed: FeedMetadata
    request_id: str
    raw_payload_sha256: str


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    event_id: str
    country_or_region: str
    importance: str
    scheduled_at_utc: datetime
    published_at_utc: datetime | None
    actual: float | None
    consensus: float | None
    prior: float | None
    revision_of: str | None
    source_url: str


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only contract. Responses must contain completed bars and provider lineage."""

    name: str

    def fetch_completed_bars(self, request: BarRequest) -> MarketDataResponse:
        raise NotImplementedError


@runtime_checkable
class EconomicCalendarProvider(Protocol):
    name: str

    def fetch_events(self, start_utc: datetime, end_utc: datetime) -> Sequence[EconomicEvent]:
        raise NotImplementedError
