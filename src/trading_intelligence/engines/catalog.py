"""Versioned inventory of school engines and their non-negotiable input boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .advanced import (
    ClassicalPatternAvailabilityEngine,
    FibonacciAnalysisEngine,
    FundamentalEventEngine,
    MarketProfileAvailabilityEngine,
    WyckoffAnalysisEngine,
)
from .base import AnalysisEngine
from .context import (
    SessionContextEngine,
    StructureEventEngine,
    SupplyDemandEngine,
    SupportResistanceEngine,
    VolumeProfileAvailabilityEngine,
)
from .technical import ICTAnalysisEngine, PriceActionEngine, QuantitativeAnalysisEngine, SMCAnalysisEngine, UnavailableInputEngine, VolumeAnalysisEngine


class EngineAvailability(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    DATA_REQUIRED = "DATA_REQUIRED"
    TEMPLATE_REQUIRED = "TEMPLATE_REQUIRED"
    RESEARCH_BACKLOG = "RESEARCH_BACKLOG"


@dataclass(frozen=True, slots=True)
class EngineDescriptor:
    engine_id: str
    school: str
    concept_ids: tuple[str, ...]
    required_inputs: tuple[str, ...]
    availability: EngineAvailability
    limitation: str


ENGINE_CATALOG: tuple[EngineDescriptor, ...] = (
    EngineDescriptor("ict", "ICT", ("ict.fair_value_gap", "ict.liquidity_sweep", "ict.order_block", "ict.ote"), ("closed OHLC", "ATR", "confirmed pivots"), EngineAvailability.IMPLEMENTED, "FVG/sweep heuristics only; no proprietary or predictive claim."),
    EngineDescriptor("smc", "SMC", ("smc.order_block", "smc.bos_choch_mss", "smc.imbalance"), ("closed OHLC", "ATR", "confirmed pivots"), EngineAvailability.IMPLEMENTED, "Order-block proxy and structure are de-correlated with related price evidence."),
    EngineDescriptor("price_action", "Price Action", ("price_action.market_structure", "price_action.swing", "price_action.imbalance"), ("closed OHLC",), EngineAvailability.IMPLEMENTED, "Confirmed pivots only; no visual post-hoc labels."),
    EngineDescriptor("structure_events", "Price Action / SMC", ("smc.bos_choch_mss",), ("closed OHLC", "confirmed pivots"), EngineAvailability.IMPLEMENTED, "CHoCH/MSS is a candidate requiring independent confirmation."),
    EngineDescriptor("wyckoff", "Wyckoff", ("wyckoff.phase", "wyckoff.spring"), ("closed OHLC", "documented OHLCV semantics for confirmation"), EngineAvailability.IMPLEMENTED, "Spring/upthrust candidate only; phases are not retrospectively asserted."),
    EngineDescriptor("supply_demand", "Supply and Demand", ("supply_demand.zone",), ("closed OHLC", "ATR"), EngineAvailability.IMPLEMENTED, "Mechanical displacement-zone proxy, not order-book supply/demand."),
    EngineDescriptor("support_resistance", "Classical Technical Analysis", ("support_resistance.swing", "classical.support_resistance"), ("closed OHLC", "confirmed pivots", "ATR"), EngineAvailability.IMPLEMENTED, "ATR-normalized pivot clusters only."),
    EngineDescriptor("classical_patterns", "Classical Technical Analysis", ("classical.chart_pattern",), ("versioned geometric template", "breakout/cost policy"), EngineAvailability.TEMPLATE_REQUIRED, "No chart pattern is labelled until a frozen geometry policy exists."),
    EngineDescriptor("fibonacci", "Fibonacci", ("fibonacci.retracement", "ict.ote"), ("closed OHLC", "confirmed opposite pivots", "ATR"), EngineAvailability.IMPLEMENTED, "Fixed-ratio feature hypothesis only."),
    EngineDescriptor("liquidity", "Liquidity Trading", ("liquidity.stop_cluster_proxy",), ("closed OHLC or L2"), EngineAvailability.IMPLEMENTED, "OHLC equal-high/low is only a liquidity proxy; actual resting liquidity needs L2."),
    EngineDescriptor("volume", "Volume", ("volume.relative_volume",), ("OHLCV with documented semantics"), EngineAvailability.IMPLEMENTED, "Relative volume only; no profile inference."),
    EngineDescriptor("volume_profile", "Volume Profile", ("volume_profile.poc",), ("venue volume-at-price or trade prints"), EngineAvailability.DATA_REQUIRED, "VAP/TPO/POC is explicitly unavailable from ordinary OHLCV."),
    EngineDescriptor("market_profile", "Market Profile", ("market_profile.auction",), ("session-aware TPO or volume-at-price"), EngineAvailability.DATA_REQUIRED, "No auction profile is inferred without session-aware distribution."),
    EngineDescriptor("order_flow", "Order Flow", ("orderflow.delta_cvd", "orderflow.footprint", "orderflow.order_book_imbalance"), ("tick trades", "quotes or L2"), EngineAvailability.DATA_REQUIRED, "Delta/CVD/footprint/order-book imbalance remain unavailable without microstructure data."),
    EngineDescriptor("quantitative", "Quantitative", ("quant.trend_baseline", "quant.regime"), ("closed OHLC",), EngineAvailability.IMPLEMENTED, "SMA/volatility baseline, not an alpha claim."),
    EngineDescriptor("fundamental_event", "Fundamental/Macro", ("fundamental.event_risk",), ("timestamped economic calendar"), EngineAvailability.IMPLEMENTED, "Event-risk context only; no fabricated news direction."),
    EngineDescriptor("ml", "Machine Learning", ("ml.regime_classifier",), ("point-in-time features", "locked OOS", "baseline comparison"), EngineAvailability.RESEARCH_BACKLOG, "No trained model or performance claim exists."),
)


def engine_catalog() -> tuple[EngineDescriptor, ...]:
    """Return the immutable capability map used for documentation/API presentation."""
    return ENGINE_CATALOG


def default_research_engines() -> tuple[AnalysisEngine, ...]:
    """Build an explicit non-networked engine set; unavailable engines report missing inputs."""
    return (
        ICTAnalysisEngine(), SMCAnalysisEngine(), PriceActionEngine(), StructureEventEngine(), WyckoffAnalysisEngine(),
        SupplyDemandEngine(), SupportResistanceEngine(), ClassicalPatternAvailabilityEngine(), FibonacciAnalysisEngine(),
        QuantitativeAnalysisEngine(), VolumeAnalysisEngine(), VolumeProfileAvailabilityEngine(), MarketProfileAvailabilityEngine(),
        UnavailableInputEngine(required_input="tick trades plus quotes/L2 for order flow, delta, CVD, and footprint", name="order_flow"),
        FundamentalEventEngine(), SessionContextEngine(),
    )
