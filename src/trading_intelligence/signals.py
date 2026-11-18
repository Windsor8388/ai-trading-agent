"""Turns an approved research decision into a *paper-candidate* signal, never a live order."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .data import validate_closed_candles
from .models import (
    DataMode, Decision, DecisionAction, InstrumentRiskSpec, MarketSnapshot, PortfolioState,
    ProposedLevels, RiskAssessment, SignalCondition, TradeSignal,
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
        conditions: list[SignalCondition] = []
        quality = validate_closed_candles(snapshot.candles(timeframe), snapshot.as_of)
        if quality.passed:
            conditions.append(SignalCondition("data.closed_candles", "data", "MET", "Completed, increasing candles passed local integrity checks."))
        else:
            reasons.extend(quality.errors)
            conditions.append(SignalCondition("data.closed_candles", "data", "BLOCKED", "; ".join(quality.errors)))
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            conditions.append(SignalCondition("analysis.decision", "analysis", "MET", f"Central decision is {decision.action.value} with de-correlated evidence."))
        else:
            reasons.append("Central decision is not directional.")
            conditions.append(SignalCondition("analysis.decision", "analysis", "BLOCKED", "Central intelligence returned NO_TRADE/WAIT."))
        if levels is None:
            conditions.append(SignalCondition("entry.levels", "entry", "UNKNOWN", "No explicit entry, stop, and target level package was supplied."))
        else:
            conditions.append(SignalCondition("entry.levels", "entry", "MET", f"Reference entry={levels.entry}; stop={levels.stop_loss}; targets={tuple(levels.targets)}."))
        if self.mode == OperatingMode.LIVE:
            reasons.append("LIVE execution is intentionally disabled; explicit future implementation and human approval are required.")
            conditions.append(SignalCondition("operating_mode.live", "operating_mode", "BLOCKED", "LIVE is disabled in this repository."))
        elif self.mode == OperatingMode.ANALYSIS_ONLY:
            reasons.append("System is in analysis-only mode; no actionable trade signal may be issued.")
            conditions.append(SignalCondition("operating_mode.paper", "operating_mode", "BLOCKED", "Analysis-only mode blocks paper candidates."))
        else:
            conditions.append(SignalCondition("operating_mode.paper", "operating_mode", "MET", "Paper mode selected; a separate lifecycle policy remains required."))
        if snapshot.feed.mode not in (DataMode.PAPER, DataMode.LIVE) or not snapshot.feed.live_feed_verified:
            reasons.append("No verified current provider connection; analysis data cannot be presented as an actionable market signal.")
            conditions.append(SignalCondition("data.provider_freshness", "data", "BLOCKED", "Feed is not a verified current PAPER/LIVE provider response."))
        else:
            conditions.append(SignalCondition("data.provider_freshness", "data", "MET", f"Verified current provider feed: {snapshot.feed.provider}."))
        risk = self.risk_engine.assess(decision.action, levels, portfolio, spec)
        if not risk.approved:
            reasons.extend(risk.reasons)
            conditions.append(SignalCondition("risk.independent_gate", "risk", "BLOCKED", "; ".join(risk.reasons)))
        else:
            conditions.append(SignalCondition("risk.independent_gate", "risk", "MET", "Independent risk engine approved reference sizing and limits."))
        action = decision.action if not reasons else DecisionAction.NO_TRADE
        status = "PAPER_CANDIDATE" if action in (DecisionAction.BUY, DecisionAction.SELL) else "BLOCKED"
        signal = TradeSignal(
            instrument=snapshot.feed.instrument, action=action, timeframe=timeframe, strategy_ids=tuple(strategy_ids),
            levels=levels if action != DecisionAction.NO_TRADE else None, confidence=decision.confidence,
            data_source=snapshot.feed.provider, created_at=snapshot.as_of, risk=risk,
            supporting_evidence=decision.selected_evidence, opposing_reasons=tuple((*decision.opposing_reasons, *reasons)),
            invalidation_conditions=decision.invalidation_conditions, status=status, conditions=tuple(conditions),
            confidence_meaning="Policy-weighted agreement of de-correlated evidence; it is not a probability of profit or a return forecast.",
        )
        return SignalBuildResult(signal, tuple(reasons))
