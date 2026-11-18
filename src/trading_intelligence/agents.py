"""Bounded research-agent coordination over accepted market snapshots.

An agent here is an orchestrator of deterministic, independently testable components;
it is not an LLM with authority to invent data, change risk, or route an order.  It can
create *research reference levels* only from closed bars and still passes every result
through the existing central, data, and risk gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .advisors.gemini import GeminiAdvisory, GeminiChartAnalyst
from .central import CentralTradingIntelligenceEngine
from .data import validate_closed_candles
from .engines.indicators import atr
from .models import (
    Decision,
    DecisionAction,
    InstrumentRiskSpec,
    MarketSnapshot,
    PortfolioState,
    ProposedLevels,
)
from .orchestration import AnalysisOrchestrator, AnalysisRun
from .risk import RiskManagementEngine
from .signals import SignalBuildResult, TradeSignalEngine


@dataclass(frozen=True, slots=True)
class ReferenceLevelPolicy:
    """Explicit research-level formula; it is not a venue quote/fill model."""

    stop_atr_multiple: float = 1.0
    target_reward_to_risk: float = 1.5

    def __post_init__(self) -> None:
        if self.stop_atr_multiple <= 0 or self.target_reward_to_risk <= 0:
            raise ValueError("Reference level ATR and reward/risk multiples must be positive.")


class ReferenceLevelPlanner:
    """Derive reproducible reference levels from a closed-bar ATR only when directional."""

    version = "0.1.0"

    def __init__(self, policy: ReferenceLevelPolicy | None = None) -> None:
        self.policy = policy or ReferenceLevelPolicy()

    def plan(self, decision: Decision, snapshot: MarketSnapshot, timeframe: str) -> ProposedLevels | None:
        if decision.action not in {DecisionAction.BUY, DecisionAction.SELL}:
            return None
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed:
            return None
        average_range = atr(candles)
        if average_range is None or average_range <= 0:
            return None
        entry = candles[-1].close
        risk_distance = average_range * self.policy.stop_atr_multiple
        if decision.action == DecisionAction.BUY:
            stop = entry - risk_distance
            target = entry + risk_distance * self.policy.target_reward_to_risk
        else:
            stop = entry + risk_distance
            target = entry - risk_distance * self.policy.target_reward_to_risk
        if stop <= 0 or target <= 0:
            return None
        return ProposedLevels(
            entry=entry,
            stop_loss=stop,
            targets=(target,),
            entry_condition=(
                "Research reference derived from latest completed close and declared ATR policy; "
                "not a venue quote, fill instruction, or live order."
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentAssessment:
    analysis: AnalysisRun
    decision: Decision
    levels: ProposedLevels | None
    signal_result: SignalBuildResult


@dataclass(frozen=True, slots=True)
class ChartAnalysisAssessment:
    """Deterministic assessment plus optional non-authoritative Gemini commentary."""

    assessment: AgentAssessment
    gemini_advisory: GeminiAdvisory | None


class MarketResearchAgent:
    """Coordinate independent engines -> de-correlated decision -> risk-gated paper signal."""

    version = "0.1.0"

    def __init__(
        self,
        analysis_orchestrator: AnalysisOrchestrator,
        central_engine: CentralTradingIntelligenceEngine,
        risk_engine: RiskManagementEngine,
        signal_engine: TradeSignalEngine,
        level_planner: ReferenceLevelPlanner | None = None,
    ) -> None:
        if signal_engine.risk_engine is not risk_engine:
            raise ValueError("Agent must use the same independent risk engine for signal construction.")
        self.analysis_orchestrator = analysis_orchestrator
        self.central_engine = central_engine
        self.risk_engine = risk_engine
        self.signal_engine = signal_engine
        self.level_planner = level_planner or ReferenceLevelPlanner()

    def assess(
        self,
        snapshot: MarketSnapshot,
        timeframes: Iterable[str],
        *,
        signal_timeframe: str,
        portfolio: PortfolioState,
        instrument_spec: InstrumentRiskSpec | None,
        strategy_ids: Sequence[str],
        reference_levels: ProposedLevels | None = None,
    ) -> AgentAssessment:
        ordered_timeframes = tuple(dict.fromkeys(timeframes))
        if signal_timeframe not in ordered_timeframes:
            raise ValueError("signal_timeframe must be included in the requested agent timeframes.")
        analysis = self.analysis_orchestrator.run(snapshot, ordered_timeframes)
        if not analysis.reports:
            raise ValueError("Agent cannot decide without any engine reports from a completed snapshot.")
        decision = self.central_engine.decide(analysis.reports)
        levels = reference_levels if reference_levels is not None else self.level_planner.plan(decision, snapshot, signal_timeframe)
        signal_result = self.signal_engine.build(
            decision, snapshot, signal_timeframe, levels, portfolio, instrument_spec, strategy_ids,
        )
        return AgentAssessment(analysis, decision, levels, signal_result)


class ChartAnalysisAgent:
    """Compose deterministic chart research with optional Gemini commentary.

    The optional adviser is called only *after* the research decision/risk gate is
    complete.  Its output is attached for operator review and is never fed back into
    engines, central scoring, risk sizing, signal conditions, or execution.
    """

    version = "0.1.0"

    def __init__(self, research_agent: MarketResearchAgent, gemini_analyst: GeminiChartAnalyst | None = None) -> None:
        self.research_agent = research_agent
        self.gemini_analyst = gemini_analyst

    def assess_chart(
        self,
        snapshot: MarketSnapshot,
        timeframes: Iterable[str],
        *,
        signal_timeframe: str,
        portfolio: PortfolioState,
        instrument_spec: InstrumentRiskSpec | None,
        strategy_ids: Sequence[str],
        reference_levels: ProposedLevels | None = None,
    ) -> ChartAnalysisAssessment:
        assessment = self.research_agent.assess(
            snapshot, timeframes, signal_timeframe=signal_timeframe, portfolio=portfolio,
            instrument_spec=instrument_spec, strategy_ids=strategy_ids, reference_levels=reference_levels,
        )
        advisory = None
        if self.gemini_analyst is not None:
            advisory = self.gemini_analyst.analyze(
                snapshot, signal_timeframe, assessment.decision, assessment.analysis.reports,
            )
        return ChartAnalysisAssessment(assessment, advisory)
