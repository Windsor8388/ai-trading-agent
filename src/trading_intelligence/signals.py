"""Turns an approved research decision into a *paper-candidate* signal, never a live order."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .data import validate_closed_candles
from .models import (
    DataMode, Decision, DecisionAction, InstrumentRiskSpec, MarketSnapshot, PortfolioState,
    ProposedLevels, RiskAssessment, TradeSignal,
)
from .risk import RiskManagementEngine


class OperatingMode(str, Enum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class SignalBuildResult:
    signal: TradeSignal
    reasons: tuple[str, ...]


class TradeSignalEngine:
    """Safety boundary. `LIVE` is deliberately refused in this repository."""

    version = "0.1.0"

    def __init__(self, risk_engine: RiskManagementEngine, mode: OperatingMode = OperatingMode.ANALYSIS_ONLY) -> None:
        self.risk_engine = risk_engine
        self.mode = mode

    def build(
        self,
        decision: Decision,
        snapshot: MarketSnapshot,
        timeframe: str,
        levels: ProposedLevels | None,
        portfolio: PortfolioState,
        spec: InstrumentRiskSpec | None,
        strategy_ids: Sequence[str],
    ) -> SignalBuildResult:
        reasons: list[str] = []
        quality = validate_closed_candles(snapshot.candles(timeframe), snapshot.as_of)
        if not quality.passed:
            reasons.extend(quality.errors)
        if self.mode == OperatingMode.LIVE:
            reasons.append("LIVE execution is intentionally disabled; explicit future implementation and human approval are required.")
        if self.mode == OperatingMode.ANALYSIS_ONLY:
            reasons.append("System is in analysis-only mode; no actionable trade signal may be issued.")
        if snapshot.feed.mode not in (DataMode.PAPER, DataMode.LIVE) or not snapshot.feed.live_feed_verified:
            reasons.append("No verified current provider connection; analysis data cannot be presented as an actionable market signal.")
        risk = self.risk_engine.assess(decision.action, levels, portfolio, spec)
        if not risk.approved:
            reasons.extend(risk.reasons)
        action = decision.action if not reasons else DecisionAction.NO_TRADE
        status = "PAPER_CANDIDATE" if action in (DecisionAction.BUY, DecisionAction.SELL) else "BLOCKED"
        signal = TradeSignal(
            instrument=snapshot.feed.instrument, action=action, timeframe=timeframe, strategy_ids=tuple(strategy_ids),
            levels=levels if action != DecisionAction.NO_TRADE else None, confidence=decision.confidence,
            data_source=snapshot.feed.provider, created_at=snapshot.as_of, risk=risk,
            supporting_evidence=decision.selected_evidence, opposing_reasons=tuple((*decision.opposing_reasons, *reasons)),
            invalidation_conditions=decision.invalidation_conditions, status=status,
        )
        return SignalBuildResult(signal, tuple(reasons))
