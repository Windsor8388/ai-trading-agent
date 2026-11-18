"""Strict domain objects shared by data, analysis, risk, and research layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


class DecisionAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


class DataMode(str, Enum):
    HISTORICAL = "HISTORICAL"
    PAPER = "PAPER"
    LIVE = "LIVE"
    SYNTHETIC = "SYNTHETIC"


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Candle:
    """A completed OHLCV bar. `open_time` and `close_time` must be timezone-aware UTC."""

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None or self.close_time.tzinfo is None:
            raise ValueError("Candle timestamps must be timezone-aware.")
        if self.close_time <= self.open_time:
            raise ValueError("Candle close_time must be after open_time.")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC invariant failed: low/open/close/high are inconsistent.")
        if self.high < self.low:
            raise ValueError("Candle high cannot be lower than low.")
        if self.volume is not None and self.volume < 0:
            raise ValueError("Candle volume cannot be negative.")


@dataclass(frozen=True, slots=True)
class FeedMetadata:
    """Lineage and safety state. It prevents an analysis result being mistaken for live data."""

    provider: str
    instrument: str
    instrument_kind: str  # e.g. XAUUSD_SPOT, GC_FUTURE, EQUITY, CRYPTO_SPOT
    price_component: str  # BID, ASK, MID, LAST, SETTLEMENT
    mode: DataMode
    retrieved_at: datetime
    vendor_symbol: str | None = None
    source_url: str | None = None
    live_feed_verified: bool = False
    quality_status: str = "UNVERIFIED"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """The only data an engine may inspect at a declared point in time.

    Candles supplied to an engine must all have `close_time <= as_of`. This models
    what was knowable then and is the first anti-look-ahead boundary.
    """

    as_of: datetime
    feed: FeedMetadata
    candles_by_timeframe: Mapping[str, Sequence[Candle]]
    economic_events: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    order_flow_available: bool = False

    def candles(self, timeframe: str) -> Sequence[Candle]:
        return self.candles_by_timeframe.get(timeframe, ())


@dataclass(frozen=True, slots=True)
class Evidence:
    """One auditable observation, not a claim of predictive truth.

    `score` is directional in [-1, 1]. `correlation_group` lets the central
    engine cap overlapping evidence, e.g. ICT/SMC/price-action structure.
    """

    evidence_id: str
    engine: str
    timeframe: str
    score: float
    confidence: float
    correlation_group: str
    measurement: str
    detail: str
    source_concept_id: str

    def __post_init__(self) -> None:
        if not -1.0 <= self.score <= 1.0:
            raise ValueError("Evidence score must be in [-1, 1].")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Evidence confidence must be in [0, 1].")


@dataclass(frozen=True, slots=True)
class EngineReport:
    engine: str
    version: str
    timeframe: str
    as_of: datetime
    proposed_direction: Direction
    confidence: float
    regime: MarketRegime
    evidence: Sequence[Evidence] = field(default_factory=tuple)
    counter_evidence: Sequence[Evidence] = field(default_factory=tuple)
    invalidation_conditions: Sequence[str] = field(default_factory=tuple)
    observations: Sequence[str] = field(default_factory=tuple)
    required_inputs_missing: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Report confidence must be in [0, 1].")


@dataclass(frozen=True, slots=True)
class Decision:
    action: DecisionAction
    confidence: float
    as_of: datetime
    selected_evidence: Sequence[Evidence]
    discarded_as_correlated: Sequence[Evidence]
    supporting_reasons: Sequence[str]
    opposing_reasons: Sequence[str]
    invalidation_conditions: Sequence[str]
    regime: MarketRegime


@dataclass(frozen=True, slots=True)
class ProposedLevels:
    entry: float
    stop_loss: float
    targets: Sequence[float]
    entry_condition: str


@dataclass(frozen=True, slots=True)
class InstrumentRiskSpec:
    """Broker/product-specific. Never infer a lot/contract multiplier from a symbol."""

    symbol: str
    contract_multiplier: float
    minimum_quantity: float
    quantity_step: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.symbol or self.contract_multiplier <= 0 or self.minimum_quantity <= 0 or self.quantity_step <= 0:
            raise ValueError("Instrument risk specification requires positive multiplier, minimum quantity, and step.")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: float
    realized_daily_loss: float = 0.0
    realized_weekly_loss: float = 0.0
    drawdown_fraction: float = 0.0
    open_positions: int = 0
    correlated_open_positions: int = 0
    consecutive_losses: int = 0
    gross_exposure_fraction: float = 0.0
    event_risk_active: bool = False
    gap_risk_active: bool = False


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_fraction: float = 0.005
    daily_loss_fraction: float = 0.02
    weekly_loss_fraction: float = 0.05
    max_drawdown_fraction: float = 0.10
    max_open_positions: int = 3
    max_correlated_positions: int = 1
    min_reward_to_risk: float = 1.0
    max_consecutive_losses: int = 3
    max_gross_exposure_fraction: float = 1.0
    gap_buffer_fraction: float = 0.0

    def __post_init__(self) -> None:
        fractions = (self.risk_per_trade_fraction, self.daily_loss_fraction, self.weekly_loss_fraction, self.max_drawdown_fraction, self.max_gross_exposure_fraction, self.gap_buffer_fraction)
        if any(value < 0 for value in fractions):
            raise ValueError("Risk-limit fractions cannot be negative.")
        if self.max_open_positions < 1 or self.max_correlated_positions < 0 or self.max_consecutive_losses < 1:
            raise ValueError("Risk count limits must be positive (correlated positions may be zero).")
        if self.min_reward_to_risk <= 0:
            raise ValueError("Minimum reward/risk must be positive.")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    approved: bool
    quantity: float | None
    max_cash_risk: float | None
    reasons: Sequence[str]
    reward_to_risk: float | None = None
    risk_per_unit: float | None = None
    worst_case_cash_risk: float | None = None


@dataclass(frozen=True, slots=True)
class TradeSignal:
    """Structured candidate signal. In this prototype all live execution remains disabled."""

    instrument: str
    action: DecisionAction
    timeframe: str
    strategy_ids: Sequence[str]
    levels: ProposedLevels | None
    confidence: float
    data_source: str
    created_at: datetime
    risk: RiskAssessment
    supporting_evidence: Sequence[Evidence]
    opposing_reasons: Sequence[str]
    invalidation_conditions: Sequence[str]
    status: str
