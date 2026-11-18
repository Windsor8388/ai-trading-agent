"""Additional independent research engines with conservative, data-aware boundaries.

These engines make only completed-bar observations. They do not claim that Wyckoff,
Fibonacci, or macro terminology provides a validated edge; each rule must still pass
its own frozen-rule, out-of-sample research programme.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from ..data import validate_closed_candles
from ..models import Direction, EngineReport, Evidence, MarketRegime, MarketSnapshot
from .base import AnalysisEngine
from .indicators import atr, confirmed_pivots, last_two, zscore


def _empty(engine: AnalysisEngine, snapshot: MarketSnapshot, timeframe: str, message: str, *, missing: Sequence[str] = ()) -> EngineReport:
    return EngineReport(engine.name, engine.version, timeframe, snapshot.as_of, Direction.FLAT, 0.0, MarketRegime.UNKNOWN, observations=(message,), required_inputs_missing=tuple(missing))


def _valid_candles(engine: AnalysisEngine, snapshot: MarketSnapshot, timeframe: str, minimum: int):
    candles = tuple(snapshot.candles(timeframe))
    quality = validate_closed_candles(candles, snapshot.as_of)
    if not quality.passed or len(candles) < minimum:
        return None
    return candles


@dataclass(frozen=True, slots=True)
class WyckoffAnalysisEngine(AnalysisEngine):
    """Mechanical spring/upthrust candidate, not a retrospective phase label."""

    name: str = "wyckoff"
    version: str = "0.1.0"
    range_lookback: int = 20
    excursion_atr_fraction: float = 0.10

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid_candles(self, snapshot, timeframe, self.range_lookback + 1)
        if candles is None:
            return _empty(self, snapshot, timeframe, f"Need >= {self.range_lookback + 1} valid completed bars for a range/spring candidate.")
        average_range = atr(candles)
        if average_range is None or average_range <= 0:
            return _empty(self, snapshot, timeframe, "ATR is unavailable; range excursion tolerance is undefined.")
        reference = candles[-self.range_lookback - 1 : -1]
        lower, upper = min(bar.low for bar in reference), max(bar.high for bar in reference)
        last = candles[-1]
        tolerance = average_range * self.excursion_atr_fraction
        score = 0.0
        event = ""
        detail = "No completed-bar spring/upthrust candidate around the declared trailing range."
        if last.low < lower - tolerance and last.close >= lower:
            score, event = 0.45, "SPRING_CANDIDATE"
            detail = "Spring candidate: price traded below trailing range low then closed back inside."
        elif last.high > upper + tolerance and last.close <= upper:
            score, event = -0.45, "UPTHRUST_CANDIDATE"
            detail = "Upthrust candidate: price traded above trailing range high then closed back inside."
        if not score:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.15, MarketRegime.RANGING, observations=(detail, "Wyckoff phase remains unclassified."))
        volumes = [bar.volume for bar in candles]
        volume_available = all(value is not None for value in volumes)
        confidence = abs(score)
        missing: tuple[str, ...] = ()
        volume_detail = "volume unavailable"
        if volume_available:
            volume_z = zscore(float(volumes[-1]), [float(value) for value in volumes[-21:-1]])
            volume_detail = f"volume_z={volume_z}"
            if volume_z is not None and volume_z >= 1:
                confidence = 0.55
        else:
            missing = ("OHLCV volume with documented semantics for Wyckoff confirmation",)
        evidence = Evidence(
            f"WYCKOFF-{event}-{timeframe}", self.name, timeframe, score, confidence, "market_structure",
            f"range=[{lower:.8g},{upper:.8g}]; tolerance={tolerance:.8g}; close={last.close:.8g}; {volume_detail}",
            detail + " This is an event candidate, not a confirmed accumulation/distribution phase.", "wyckoff.spring",
        )
        invalidation = (f"Close back below range low {lower:.8g} invalidates spring premise.",) if score > 0 else (f"Close back above range high {upper:.8g} invalidates upthrust premise.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.BUY if score > 0 else Direction.SELL, confidence, MarketRegime.RANGING, evidence=(evidence,), invalidation_conditions=invalidation, observations=(detail,), required_inputs_missing=missing)


@dataclass(frozen=True, slots=True)
class FibonacciAnalysisEngine(AnalysisEngine):
    """Confirmed-swing retracement proximity; anchors and tolerance are explicit."""

    name: str = "fibonacci"
    version: str = "0.1.0"
    ratios: tuple[float, ...] = (0.382, 0.5, 0.618, 0.705)
    atr_tolerance_fraction: float = 0.15

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid_candles(self, snapshot, timeframe, 20)
        if candles is None:
            return _empty(self, snapshot, timeframe, "Need >= 20 valid completed bars for confirmed Fibonacci anchors.")
        average_range = atr(candles)
        pivots = confirmed_pivots(candles)
        if average_range is None or average_range <= 0 or len(pivots) < 2:
            return _empty(self, snapshot, timeframe, "ATR or two confirmed opposite pivots are unavailable for Fibonacci anchors.")
        anchor_end = pivots[-1]
        anchor_start = next((pivot for pivot in reversed(pivots[:-1]) if pivot.kind != anchor_end.kind), None)
        if anchor_start is None:
            return _empty(self, snapshot, timeframe, "No opposite confirmed pivot precedes the current anchor.")
        span = abs(anchor_end.price - anchor_start.price)
        if span <= 0:
            return _empty(self, snapshot, timeframe, "Confirmed Fibonacci anchor span is zero.")
        bullish_leg = anchor_start.kind == "LOW" and anchor_end.kind == "HIGH"
        close = candles[-1].close
        retracement = (anchor_end.price - close) / span if bullish_leg else (close - anchor_end.price) / span
        nearest = min(self.ratios, key=lambda ratio: abs(retracement - ratio))
        tolerance = (average_range * self.atr_tolerance_fraction) / span
        if not 0 < retracement < 1 or abs(retracement - nearest) > tolerance:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.1, MarketRegime.UNKNOWN, observations=(f"Retracement={retracement:.3f}; not within configured tolerance of {self.ratios}.",))
        score = 0.30 if bullish_leg else -0.30
        evidence = Evidence(
            f"FIB-RETRACE-{timeframe}-{anchor_start.index}-{anchor_end.index}", self.name, timeframe, score, abs(score), "market_structure",
            f"start={anchor_start.kind}@{anchor_start.price:.8g}; end={anchor_end.kind}@{anchor_end.price:.8g}; retracement={retracement:.4f}; nearest_ratio={nearest:.3f}; tolerance={tolerance:.4f}",
            "Price is near a fixed-ratio retracement of a completed confirmed swing; a feature hypothesis, not a causal claim.", "fibonacci.retracement",
        )
        invalidation = (f"Close below anchor low {anchor_start.price:.8g} invalidates bullish swing anchor.",) if bullish_leg else (f"Close above anchor high {anchor_start.price:.8g} invalidates bearish swing anchor.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.BUY if score > 0 else Direction.SELL, evidence.confidence, MarketRegime.UNKNOWN, evidence=(evidence,), invalidation_conditions=invalidation)


@dataclass(frozen=True, slots=True)
class FundamentalEventEngine(AnalysisEngine):
    """Calendar-risk context only; no directional news sentiment is fabricated."""

    name: str = "fundamental_event"
    version: str = "0.1.0"
    embargo_minutes: int = 30
    minimum_importance: tuple[str, ...] = ("HIGH", "CRITICAL")

    @staticmethod
    def _event_time(event: Mapping[str, object]) -> datetime | None:
        value = event.get("scheduled_at_utc", event.get("scheduled_at"))
        return value if isinstance(value, datetime) else None

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        if not snapshot.economic_events:
            return _empty(self, snapshot, timeframe, "Economic calendar is not connected; no fundamental/news assertion is made.", missing=("timestamped licensed economic calendar or news feed",))
        nearby: list[str] = []
        for event in snapshot.economic_events:
            if not isinstance(event, Mapping):
                continue
            scheduled = self._event_time(event)
            importance = str(event.get("importance", "")).upper()
            if scheduled is None or scheduled.tzinfo is None or importance not in self.minimum_importance:
                continue
            minutes = abs((scheduled - snapshot.as_of).total_seconds()) / 60
            if minutes <= self.embargo_minutes:
                nearby.append(f"{event.get('event_id', 'event')} ({importance}) at {scheduled.isoformat()}")
        if nearby:
            return EngineReport(
                self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.0, MarketRegime.HIGH_VOLATILITY,
                observations=("Event-risk embargo context: " + "; ".join(nearby),),
                invalidation_conditions=("Do not infer news direction; reassess after event publication and data-quality verification.",),
            )
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.0, MarketRegime.UNKNOWN, observations=("Calendar data supplied; no configured high-impact event falls within embargo window.",))


@dataclass(frozen=True, slots=True)
class ResearchBacklogEngine(AnalysisEngine):
    """Explicit report for a school whose model/data validation has not been approved."""

    required_input: str = ""
    limitation: str = ""
    name: str = "research_backlog"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        return _empty(
            self, snapshot, timeframe,
            f"{self.name} is not enabled: {self.limitation}",
            missing=(self.required_input,),
        )


@dataclass(frozen=True, slots=True)
class MarketProfileAvailabilityEngine(AnalysisEngine):
    """Prevents an OHLC-only approximation being mislabeled as TPO/auction profile."""

    name: str = "market_profile"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        return _empty(
            self, snapshot, timeframe,
            "Market Profile is unavailable: a session-aware TPO or trade/volume-at-price distribution is required.",
            missing=("session-aware TPO or volume-at-price distribution",),
        )


@dataclass(frozen=True, slots=True)
class ClassicalPatternAvailabilityEngine(AnalysisEngine):
    """Makes missing frozen geometry templates explicit instead of visual post-hoc labels."""

    name: str = "classical_patterns"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        return _empty(
            self, snapshot, timeframe,
            "Classical chart-pattern recognition is not enabled until a versioned geometric template, tolerance, and breakout policy are supplied.",
            missing=("versioned classical-pattern geometry/template policy",),
        )
