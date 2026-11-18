"""Conservative market-context engines with explicit data boundaries.

These rules are auditable hypotheses over completed candles. They neither claim that a
named discretionary school is validated nor convert OHLCV into unavailable order-flow
or volume-at-price data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..data import validate_closed_candles
from ..models import Direction, EngineReport, Evidence, MarketRegime, MarketSnapshot
from ..research.analytics import DEFAULT_UTC_SESSIONS, classify_session
from .base import AnalysisEngine
from .indicators import atr, confirmed_pivots, last_two


def _empty(engine: AnalysisEngine, snapshot: MarketSnapshot, timeframe: str, message: str, *, missing: Sequence[str] = ()) -> EngineReport:
    return EngineReport(engine.name, engine.version, timeframe, snapshot.as_of, Direction.FLAT, 0.0, MarketRegime.UNKNOWN, observations=(message,), required_inputs_missing=tuple(missing))


def _valid(engine: AnalysisEngine, snapshot: MarketSnapshot, timeframe: str, minimum: int) -> tuple[object, ...] | None:
    candles = snapshot.candles(timeframe)
    quality = validate_closed_candles(candles, snapshot.as_of)
    if not quality.passed or len(candles) < minimum:
        return None
    return tuple(candles)


@dataclass(frozen=True, slots=True)
class StructureEventEngine(AnalysisEngine):
    """Closed-bar BOS and CHoCH/MSS-candidate detector, de-correlated as structure."""

    name: str = "structure_events"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid(self, snapshot, timeframe, 15)
        if candles is None:
            return _empty(self, snapshot, timeframe, "Need >= 15 valid completed bars for BOS/CHoCH/MSS inspection.")
        pivots = confirmed_pivots(candles)
        highs, lows = last_two(pivots, "HIGH"), last_two(pivots, "LOW")
        if not highs or not lows:
            return _empty(self, snapshot, timeframe, "Need two confirmed highs and lows before classifying a structure event.")
        higher_highs, higher_lows = highs[1].price > highs[0].price, lows[1].price > lows[0].price
        lower_highs, lower_lows = highs[1].price < highs[0].price, lows[1].price < lows[0].price
        close = candles[-1].close
        event: str | None = None
        score = 0.0
        detail = "No configured closed-bar structure break."
        if higher_highs and higher_lows and close > highs[1].price:
            event, score, detail = "BOS_BULLISH", 0.60, "Bullish BOS: close broke the latest confirmed swing high after HH/HL context."
        elif lower_highs and lower_lows and close < lows[1].price:
            event, score, detail = "BOS_BEARISH", -0.60, "Bearish BOS: close broke the latest confirmed swing low after LH/LL context."
        elif higher_highs and higher_lows and close < lows[1].price:
            event, score, detail = "CHOCH_MSS_BEARISH_CANDIDATE", -0.55, "Bearish CHoCH/MSS candidate: close broke protected swing low after HH/HL context; confirm separately."
        elif lower_highs and lower_lows and close > highs[1].price:
            event, score, detail = "CHOCH_MSS_BULLISH_CANDIDATE", 0.55, "Bullish CHoCH/MSS candidate: close broke protected swing high after LH/LL context; confirm separately."
        if event is None:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.15, MarketRegime.RANGING, observations=(detail,))
        evidence = Evidence(
            f"STRUCTURE-{event}-{timeframe}", self.name, timeframe, score, abs(score), "market_structure",
            f"event={event}; close={close:.8g}; swing_high={highs[1].price:.8g}; swing_low={lows[1].price:.8g}",
            detail, "price_action.market_structure",
        )
        invalidation = (f"Closed bar back below {lows[1].price:.8g} requires reclassification.",) if score > 0 else (f"Closed bar back above {highs[1].price:.8g} requires reclassification.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.BUY if score > 0 else Direction.SELL, evidence.confidence, MarketRegime.UNKNOWN, evidence=(evidence,), invalidation_conditions=invalidation, observations=(detail,))


@dataclass(frozen=True, slots=True)
class SupportResistanceEngine(AnalysisEngine):
    """Pivot-touch support/resistance hypothesis; it does not manufacture horizontal levels."""

    name: str = "support_resistance"
    version: str = "0.1.0"
    atr_tolerance_fraction: float = 0.25

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid(self, snapshot, timeframe, 20)
        if candles is None:
            return _empty(self, snapshot, timeframe, "Need >= 20 valid completed bars for support/resistance clustering.")
        average_range = atr(candles)
        if average_range is None or average_range <= 0:
            return _empty(self, snapshot, timeframe, "ATR is unavailable; support/resistance clustering tolerance is undefined.")
        pivots = confirmed_pivots(candles)
        tolerance = average_range * self.atr_tolerance_fraction
        candidates: list[tuple[str, float, int]] = []
        for kind, label in (("LOW", "SUPPORT"), ("HIGH", "RESISTANCE")):
            points = [item.price for item in pivots if item.kind == kind]
            for price in points:
                touches = sum(abs(other - price) <= tolerance for other in points)
                if touches >= 2:
                    candidates.append((label, price, touches))
        if not candidates:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.1, MarketRegime.UNKNOWN, observations=("No two-touch pivot cluster within configured ATR tolerance.",))
        close, last = candles[-1].close, candles[-1]
        nearest = min(candidates, key=lambda item: abs(close - item[1]))
        label, level, touches = nearest
        near = abs(close - level) <= tolerance
        score = 0.0
        if near and label == "SUPPORT" and last.close > last.open:
            score = 0.35
        elif near and label == "RESISTANCE" and last.close < last.open:
            score = -0.35
        if score == 0:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.2, MarketRegime.UNKNOWN, observations=(f"Nearest {label.lower()} cluster={level:.8g}, touches={touches}, near={near}; no configured bounce confirmation.",))
        evidence = Evidence(
            f"SR-{label}-{timeframe}", self.name, timeframe, score, abs(score), "support_resistance",
            f"level={level:.8g}; touches={touches}; tolerance={tolerance:.8g}; close={close:.8g}",
            f"{label.title()} pivot cluster with a completed directional bounce heuristic.", "classical.support_resistance",
        )
        invalidation = (f"Close below support cluster {level:.8g} invalidates the bounce premise.",) if score > 0 else (f"Close above resistance cluster {level:.8g} invalidates the rejection premise.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.BUY if score > 0 else Direction.SELL, evidence.confidence, MarketRegime.UNKNOWN, evidence=(evidence,), invalidation_conditions=invalidation)


@dataclass(frozen=True, slots=True)
class SupplyDemandEngine(AnalysisEngine):
    """Explicit displacement/last-opposite-candle zone proxy, distinct from venue order book data."""

    name: str = "supply_demand"
    version: str = "0.1.0"
    displacement_atr_multiple: float = 1.25

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid(self, snapshot, timeframe, 20)
        if candles is None:
            return _empty(self, snapshot, timeframe, "Need >= 20 valid completed bars for supply/demand-zone proxy.")
        average_range = atr(candles)
        if average_range is None or average_range <= 0:
            return _empty(self, snapshot, timeframe, "ATR is unavailable; displacement threshold is undefined.")
        zone: tuple[str, float, float, int] | None = None
        for index in range(len(candles) - 1, 1, -1):
            body = candles[index].close - candles[index].open
            direction = 1 if body > 0 else -1
            if abs(body) < average_range * self.displacement_atr_multiple:
                continue
            for prior_index in range(index - 1, max(-1, index - 4), -1):
                prior_body = candles[prior_index].close - candles[prior_index].open
                if prior_body * direction < 0:
                    zone = ("DEMAND" if direction > 0 else "SUPPLY", candles[prior_index].low, candles[prior_index].high, prior_index)
                    break
            if zone:
                break
        if zone is None:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.1, MarketRegime.UNKNOWN, observations=("No configured displacement followed by an opposite-candle zone proxy.",))
        kind, lower, upper, origin = zone
        close = candles[-1].close
        score = 0.35 if kind == "DEMAND" and close >= lower else -0.35 if kind == "SUPPLY" and close <= upper else 0.0
        if score == 0:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.15, MarketRegime.UNKNOWN, observations=(f"{kind.title()} proxy [{lower:.8g}, {upper:.8g}] from closed bar {origin} is invalidated/untested by current close.",))
        evidence = Evidence(
            f"SD-{kind}-{timeframe}-{origin}", self.name, timeframe, score, abs(score), "market_structure",
            f"zone=[{lower:.8g},{upper:.8g}]; origin={origin}; close={close:.8g}; atr={average_range:.8g}",
            f"{kind.title()} zone proxy from last opposite candle before configured completed-bar displacement.", "supply_demand.zone",
        )
        invalidation = (f"Close below demand-zone low {lower:.8g} invalidates this proxy.",) if score > 0 else (f"Close above supply-zone high {upper:.8g} invalidates this proxy.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.BUY if score > 0 else Direction.SELL, evidence.confidence, MarketRegime.UNKNOWN, evidence=(evidence,), invalidation_conditions=invalidation)


@dataclass(frozen=True, slots=True)
class SessionContextEngine(AnalysisEngine):
    """Labels generic UTC session context; it is not an exchange/DST trading-hours policy."""

    name: str = "session_context"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = _valid(self, snapshot, timeframe, 1)
        if candles is None:
            return _empty(self, snapshot, timeframe, "No valid completed bar to assign a UTC session label.")
        label = classify_session(candles[-1].close_time, DEFAULT_UTC_SESSIONS)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.0, MarketRegime.UNKNOWN, observations=(f"Generic UTC session={label}; not an exchange calendar or DST policy.",))


@dataclass(frozen=True, slots=True)
class VolumeProfileAvailabilityEngine(AnalysisEngine):
    """Explicitly refuses to fabricate VAP/TPO/POC from OHLCV candle volume."""

    name: str = "volume_profile"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        return _empty(
            self, snapshot, timeframe,
            "Volume profile is unavailable: OHLCV volume cannot establish venue volume-at-price, TPO, POC, or value area.",
            missing=("venue volume-at-price or TPO distribution with documented session semantics",),
        )
