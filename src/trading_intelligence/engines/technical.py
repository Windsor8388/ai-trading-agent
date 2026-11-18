"""Versioned, deliberately conservative first-pass school engines.

The engines encode operational hypotheses, not claims that a named trading school has
been empirically validated. Their concepts are tied to IDs in knowledge/concepts.json.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Sequence

from .base import AnalysisEngine
from .indicators import atr, confirmed_pivots, last_two, realized_volatility, simple_return_series, sma, zscore
from ..data import validate_closed_candles
from ..models import Direction, EngineReport, Evidence, MarketRegime, MarketSnapshot


def _empty_report(engine: AnalysisEngine, snapshot: MarketSnapshot, timeframe: str, message: str, *, missing: Sequence[str] = ()) -> EngineReport:
    return EngineReport(
        engine=engine.name, version=engine.version, timeframe=timeframe, as_of=snapshot.as_of,
        proposed_direction=Direction.FLAT, confidence=0.0, regime=MarketRegime.UNKNOWN,
        observations=(message,), required_inputs_missing=tuple(missing),
    )


def _direction(score: float) -> Direction:
    return Direction.BUY if score > 0 else Direction.SELL if score < 0 else Direction.FLAT


def _structure(candles) -> tuple[MarketRegime, float, str, float | None, float | None]:
    pivots = confirmed_pivots(candles)
    highs, lows = last_two(pivots, "HIGH"), last_two(pivots, "LOW")
    if not highs or not lows:
        return MarketRegime.UNKNOWN, 0.0, "Fewer than two confirmed swing highs and lows.", None, None
    higher_highs, higher_lows = highs[1].price > highs[0].price, lows[1].price > lows[0].price
    lower_highs, lower_lows = highs[1].price < highs[0].price, lows[1].price < lows[0].price
    latest = candles[-1].close
    if higher_highs and higher_lows:
        score = 0.55 + (0.15 if latest > highs[1].price else 0.0)
        return MarketRegime.TRENDING_UP, score, "Confirmed higher-high and higher-low sequence.", highs[1].price, lows[1].price
    if lower_highs and lower_lows:
        score = -0.55 - (0.15 if latest < lows[1].price else 0.0)
        return MarketRegime.TRENDING_DOWN, score, "Confirmed lower-high and lower-low sequence.", highs[1].price, lows[1].price
    return MarketRegime.RANGING, 0.0, "Confirmed swings are mixed; no directional structure.", highs[1].price, lows[1].price


@dataclass(frozen=True, slots=True)
class PriceActionEngine(AnalysisEngine):
    name: str = "price_action"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed:
            return _empty_report(self, snapshot, timeframe, f"Data rejected: {'; '.join(quality.errors)}")
        if len(candles) < 15:
            return _empty_report(self, snapshot, timeframe, "Need >= 15 completed bars for confirmed structure.")
        regime, score, detail, swing_high, swing_low = _structure(candles)
        if score == 0:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.2, regime,
                                observations=(detail,), invalidation_conditions=("Wait for a confirmed swing sequence or break.",))
        bos = (score > 0 and candles[-1].close > (swing_high or float("inf"))) or (score < 0 and candles[-1].close < (swing_low or float("-inf")))
        evidence = Evidence(
            evidence_id=f"PA-STRUCTURE-{timeframe}", engine=self.name, timeframe=timeframe, score=score,
            confidence=min(abs(score), 0.75), correlation_group="market_structure",
            measurement=f"last_close={candles[-1].close:.5f}; swing_high={swing_high}; swing_low={swing_low}; bos={bos}",
            detail=detail + (" Close also broke the latest relevant swing." if bos else ""),
            source_concept_id="price_action.market_structure",
        )
        invalidation = (f"Close below confirmed swing low {swing_low:.5f}.",) if score > 0 else (f"Close above confirmed swing high {swing_high:.5f}.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, _direction(score), evidence.confidence, regime,
                            evidence=(evidence,), invalidation_conditions=invalidation, observations=(detail,))


@dataclass(frozen=True, slots=True)
class ICTAnalysisEngine(AnalysisEngine):
    """Heuristic FVG + equal-high/low sweep detector; no proprietary claim is implied."""
    name: str = "ict"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed or len(candles) < 22:
            return _empty_report(self, snapshot, timeframe, "Need valid data and >= 22 completed bars for ICT heuristics.")
        average_range = atr(candles)
        if not average_range or average_range == 0:
            return _empty_report(self, snapshot, timeframe, "ATR unavailable; cannot size liquidity tolerance.")
        fvg: tuple[str, float, float, int] | None = None
        for i in range(2, len(candles)):
            # A three-bar imbalance is known only after bar i closes.
            if candles[i - 2].high < candles[i].low:
                fvg = ("BULLISH", candles[i - 2].high, candles[i].low, i)
            elif candles[i - 2].low > candles[i].high:
                fvg = ("BEARISH", candles[i].high, candles[i - 2].low, i)
        observations: list[str] = []
        candidates: list[Evidence] = []
        current = candles[-1].close
        if fvg:
            kind, lower, upper, created = fvg
            in_zone = lower <= current <= upper
            score = 0.45 if kind == "BULLISH" else -0.45
            candidates.append(Evidence(
                f"ICT-FVG-{timeframe}-{created}", self.name, timeframe, score, 0.45, "market_structure",
                f"{kind}; zone=[{lower:.5f},{upper:.5f}]; current={current:.5f}; in_zone={in_zone}",
                "Latest detected three-bar fair-value-gap heuristic.", "ict.fair_value_gap"))
            observations.append(f"{kind} FVG [{lower:.5f}, {upper:.5f}] detected at closed bar {created}; in_zone={in_zone}.")
        pivots = confirmed_pivots(candles)
        tolerance = average_range * 0.15
        for kind in ("HIGH", "LOW"):
            points = [pivot for pivot in pivots if pivot.kind == kind]
            if len(points) < 2:
                continue
            previous, latest = points[-2], points[-1]
            if abs(previous.price - latest.price) > tolerance:
                continue
            pool = (previous.price + latest.price) / 2
            last = candles[-1]
            if kind == "HIGH" and last.high > pool + tolerance and last.close < pool:
                candidates.append(Evidence(
                    f"ICT-BSL-SWEEP-{timeframe}", self.name, timeframe, -0.7, 0.7, "market_structure",
                    f"equal_high_pool={pool:.5f}; tolerance={tolerance:.5f}; last_high={last.high:.5f}; last_close={last.close:.5f}",
                    "Buyside-liquidity sweep heuristic: traded above near-equal highs then closed below.", "ict.liquidity_sweep"))
                observations.append("Potential buyside-liquidity sweep on completed bar.")
            if kind == "LOW" and last.low < pool - tolerance and last.close > pool:
                candidates.append(Evidence(
                    f"ICT-SSL-SWEEP-{timeframe}", self.name, timeframe, 0.7, 0.7, "market_structure",
                    f"equal_low_pool={pool:.5f}; tolerance={tolerance:.5f}; last_low={last.low:.5f}; last_close={last.close:.5f}",
                    "Sellside-liquidity sweep heuristic: traded below near-equal lows then closed above.", "ict.liquidity_sweep"))
                observations.append("Potential sellside-liquidity sweep on completed bar.")
        if not candidates:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.1, MarketRegime.UNKNOWN,
                                observations=("No configured FVG/sweep condition on completed bars.",))
        # A reversal sweep gets priority over an older FVG; this remains a single correlated group.
        selected = max(candidates, key=lambda evidence: abs(evidence.score))
        invalidation = ("A close back beyond the swept equal-high pool invalidates bearish sweep premise.",) if selected.score < 0 else ("A close back beyond the swept equal-low pool invalidates bullish sweep premise.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, _direction(selected.score), selected.confidence,
                            MarketRegime.UNKNOWN, evidence=tuple(candidates), invalidation_conditions=invalidation,
                            observations=tuple(observations))


@dataclass(frozen=True, slots=True)
class SMCAnalysisEngine(AnalysisEngine):
    """SMC rules are maintained separately from ICT despite shared market-structure inputs."""
    name: str = "smc"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed or len(candles) < 20:
            return _empty_report(self, snapshot, timeframe, "Need valid data and >= 20 completed bars for SMC structure.")
        value_at_risk = atr(candles)
        regime, structure_score, detail, swing_high, swing_low = _structure(candles)
        if not value_at_risk or structure_score == 0:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.15, regime,
                                observations=(detail, "No qualified SMC displacement/order-block candidate."))
        # Last opposite candle preceding a >=1.25 ATR body is an explicit, testable OB proxy.
        direction = 1 if structure_score > 0 else -1
        block = None
        for i in range(len(candles) - 1, max(0, len(candles) - 8), -1):
            body = candles[i].close - candles[i].open
            if body * direction >= 1.25 * value_at_risk:
                for j in range(i - 1, max(-1, i - 6), -1):
                    prior_body = candles[j].close - candles[j].open
                    if prior_body * direction < 0:
                        block = (j, candles[j].low, candles[j].high, i)
                        break
            if block:
                break
        if not block:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.2, regime,
                                observations=(detail, "Structure exists but no configured displacement + opposing-candle order block."))
        index, lower, upper, displacement_index = block
        last_close = candles[-1].close
        holds = last_close > lower if direction > 0 else last_close < upper
        score = 0.6 * direction if holds else 0.35 * direction
        evidence = Evidence(
            f"SMC-OB-{timeframe}-{index}", self.name, timeframe, score, abs(score), "market_structure",
            f"ob=[{lower:.5f},{upper:.5f}]; displacement_bar={displacement_index}; current={last_close:.5f}; holds={holds}",
            "Last opposing candle before configured ATR displacement, conditioned on independently confirmed structure.",
            "smc.order_block",
        )
        invalidation = (f"Close below order-block low {lower:.5f}.",) if direction > 0 else (f"Close above order-block high {upper:.5f}.",)
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, _direction(score), abs(score), regime,
                            evidence=(evidence,), invalidation_conditions=invalidation,
                            observations=(detail, f"Order-block proxy from bar {index}; holds={holds}."))


@dataclass(frozen=True, slots=True)
class VolumeAnalysisEngine(AnalysisEngine):
    name: str = "volume"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed or len(candles) < 22:
            return _empty_report(self, snapshot, timeframe, "Need valid data and >= 22 completed bars for volume analysis.")
        volumes = [bar.volume for bar in candles if bar.volume is not None]
        if len(volumes) != len(candles) or all(value == 0 for value in volumes):
            return _empty_report(self, snapshot, timeframe, "Volume unavailable; cannot infer volume confirmation.", missing=("OHLCV volume with documented semantics",))
        z = zscore(volumes[-1], volumes[-21:-1])
        if z is None or z < 1.0:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.1, MarketRegime.UNKNOWN,
                                observations=(f"Last volume z-score={z}; no high-volume confirmation threshold met.",))
        score = 0.4 if candles[-1].close > candles[-1].open else -0.4
        evidence = Evidence(
            f"VOL-EXPANSION-{timeframe}", self.name, timeframe, score, 0.4, "volume_confirmation",
            f"last_volume={volumes[-1]:.5f}; trailing_z={z:.3f}",
            "Directional completed candle with volume >= one standard deviation above prior 20 bars.",
            "volume.relative_volume",
        )
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, _direction(score), 0.4, MarketRegime.UNKNOWN,
                            evidence=(evidence,), invalidation_conditions=("Subsequent price failure makes volume directional interpretation invalid.",))


@dataclass(frozen=True, slots=True)
class QuantitativeAnalysisEngine(AnalysisEngine):
    name: str = "quantitative"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed or len(candles) < 61:
            return _empty_report(self, snapshot, timeframe, "Need valid data and >= 61 completed bars for 20/50 trend and volatility.")
        closes = [bar.close for bar in candles]
        fast, slow = sma(closes, 20), sma(closes, 50)
        volatility = realized_volatility(candles, 20)
        if fast is None or slow is None or volatility is None:
            return _empty_report(self, snapshot, timeframe, "Quantitative inputs incomplete.")
        vol_history = []
        for end in range(40, len(candles) + 1):
            candidate = realized_volatility(candles[:end], 20)
            if candidate is not None:
                vol_history.append(candidate)
        percentile_proxy = sum(item <= volatility for item in vol_history) / len(vol_history) if vol_history else 0.5
        if percentile_proxy > 0.90:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.2, MarketRegime.HIGH_VOLATILITY,
                                observations=(f"Realized-volatility percentile proxy={percentile_proxy:.2f}; configured high-volatility pause.",),
                                invalidation_conditions=("Re-evaluate after volatility normalizes; no directional claim is made.",))
        distance = (fast - slow) / slow if slow else 0.0
        if abs(distance) < 0.001:
            return EngineReport(self.name, self.version, timeframe, snapshot.as_of, Direction.FLAT, 0.25, MarketRegime.RANGING,
                                observations=(f"20/50 SMA distance={distance:.4%}; configured as range/weak trend.",))
        score = 0.5 if distance > 0 else -0.5
        regime = MarketRegime.TRENDING_UP if score > 0 else MarketRegime.TRENDING_DOWN
        evidence = Evidence(
            f"QUANT-TREND-{timeframe}", self.name, timeframe, score, 0.5, "statistical_trend",
            f"sma20={fast:.5f}; sma50={slow:.5f}; distance={distance:.4%}; rv20={volatility:.4%}; rv_percentile_proxy={percentile_proxy:.2f}",
            "Simple moving-average direction under a documented volatility gate; baseline, not ML.", "quant.trend_baseline",
        )
        return EngineReport(self.name, self.version, timeframe, snapshot.as_of, _direction(score), 0.5, regime,
                            evidence=(evidence,), invalidation_conditions=("20/50 average cross or volatility-gate breach.",))


@dataclass(frozen=True, slots=True)
class UnavailableInputEngine(AnalysisEngine):
    """Makes a missing order-flow or news feed explicit rather than fabricating a result."""
    required_input: str = ""
    name: str = "unavailable"
    version: str = "0.1.0"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        return _empty_report(self, snapshot, timeframe, f"Engine not run: {self.required_input} is not connected.", missing=(self.required_input,))
