"""Canonical instrument identity registry for research and provider configuration.

The registry intentionally separates a research instrument from a vendor symbol and
from execution specifications.  It supports the asset classes in scope without
pretending that a provider entitlement, exchange calendar, contract multiplier, or
live feed exists.  Provider adapters must still receive an explicit mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class AssetClass(str, Enum):
    METAL_SPOT = "METAL_SPOT"
    FX_SPOT = "FX_SPOT"
    INDEX_CASH = "INDEX_CASH"
    EQUITY = "EQUITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    FUTURE = "FUTURE"


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    """Stable research identity; execution terms remain venue-specific.

    ``canonical_id`` deliberately does not embed an OANDA, exchange, or TradingView
    symbol.  ``session_policy_required`` prevents daily/session-sensitive processing
    until a provider- and venue-specific calendar has been approved.
    """

    canonical_id: str
    display_name: str
    asset_class: AssetClass
    instrument_kind: str
    quote_currency: str
    default_price_component: str
    session_policy_required: bool
    provider_mapping_required: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.canonical_id or self.canonical_id.strip() != self.canonical_id:
            raise ValueError("Canonical instrument id must be a non-empty trimmed string.")
        if not self.display_name or not self.instrument_kind or not self.quote_currency:
            raise ValueError("Instrument definitions require display, kind, and quote currency.")
        if self.default_price_component not in {"BID", "ASK", "MID", "LAST", "SETTLEMENT"}:
            raise ValueError("Instrument price component must be BID, ASK, MID, LAST, or SETTLEMENT.")


class InstrumentRegistry:
    """Immutable lookup used to gate provider mappings and analysis universe selection."""

    version = "0.1.0"

    def __init__(self, instruments: Iterable[InstrumentDefinition]) -> None:
        values = tuple(instruments)
        self._by_id = {instrument.canonical_id: instrument for instrument in values}
        if not self._by_id or len(self._by_id) != len(values):
            raise ValueError("Instrument registry must be non-empty with unique canonical ids.")

    def get(self, canonical_id: str) -> InstrumentDefinition:
        try:
            return self._by_id[canonical_id]
        except KeyError as exc:
            raise KeyError(f"Unknown canonical instrument: {canonical_id}") from exc

    def supports(self, canonical_id: str) -> bool:
        return canonical_id in self._by_id

    def list(self, *, asset_class: AssetClass | None = None) -> tuple[InstrumentDefinition, ...]:
        selected = (
            instrument for instrument in self._by_id.values()
            if asset_class is None or instrument.asset_class == asset_class
        )
        return tuple(sorted(selected, key=lambda instrument: instrument.canonical_id))


DEFAULT_INSTRUMENTS: tuple[InstrumentDefinition, ...] = (
    InstrumentDefinition(
        "XAUUSD", "XAU/USD spot", AssetClass.METAL_SPOT, "XAUUSD_SPOT", "USD", "MID", True,
        notes="Spot/CFD quotation is provider-specific; it is never substituted with GC futures.",
    ),
    InstrumentDefinition(
        "EURUSD", "EUR/USD spot", AssetClass.FX_SPOT, "FX_SPOT", "USD", "MID", False,
        notes="Vendor must document bid/ask/mid component and FX session/holiday handling.",
    ),
    InstrumentDefinition(
        "US500", "US large-cap cash index", AssetClass.INDEX_CASH, "INDEX_CASH", "USD", "LAST", True,
        notes="Cash index, CFD, ETF, and futures are distinct products and require explicit mappings.",
    ),
    InstrumentDefinition(
        "AAPL", "Apple common equity", AssetClass.EQUITY, "EQUITY", "USD", "LAST", True,
        notes="Corporate-action adjustment, exchange calendar, and regular/extended session policy are required.",
    ),
    InstrumentDefinition(
        "BTCUSD", "Bitcoin / USD spot", AssetClass.CRYPTO_SPOT, "CRYPTO_SPOT", "USD", "LAST", False,
        notes="Exchange/venue, quote currency, and 24/7 downtime policy remain explicit provider metadata.",
    ),
    InstrumentDefinition(
        "GC", "COMEX Gold future", AssetClass.FUTURE, "GC_FUTURE", "USD", "LAST", True,
        notes="A contract month or separately documented continuous-roll policy is mandatory before analysis.",
    ),
)


def default_instrument_registry() -> InstrumentRegistry:
    """Return a fresh immutable registry for the supported research universe."""
    return InstrumentRegistry(DEFAULT_INSTRUMENTS)
