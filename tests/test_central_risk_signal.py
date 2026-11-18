from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.central import CentralTradingIntelligenceEngine
from trading_intelligence.models import (
    Candle, DataMode, DecisionAction, Direction, EngineReport, Evidence, FeedMetadata,
    InstrumentRiskSpec, MarketRegime, MarketSnapshot, PortfolioState, ProposedLevels,
)
from trading_intelligence.risk import RiskManagementEngine
from trading_intelligence.signals import OperatingMode, TradeSignalEngine


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def evidence(engine: str, group: str, score: float) -> Evidence:
    return Evidence(f"{engine}-{group}", engine, "H1", score, abs(score), group, "unit measurement", "unit detail", "unit.concept")


def report(engine: str, item: Evidence) -> EngineReport:
    return EngineReport(engine, "test", "H1", NOW, Direction.BUY if item.score > 0 else Direction.SELL, abs(item.score), MarketRegime.TRENDING_UP, evidence=(item,))


def test_snapshot() -> MarketSnapshot:
    bar = Candle(NOW - timedelta(hours=1), NOW, 100, 101, 99, 100.5, 100)
    return MarketSnapshot(NOW, FeedMetadata("not-connected", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.SYNTHETIC, NOW), {"H1": (bar,)})


class CentralRiskSignalTests(unittest.TestCase):
    def test_correlated_schools_count_once_and_can_not_force_trade(self) -> None:
        reports = [
            report("ict", evidence("ict", "market_structure", .8)),
            report("smc", evidence("smc", "market_structure", .7)),
            report("price_action", evidence("price_action", "market_structure", .6)),
        ]
        decision = CentralTradingIntelligenceEngine().decide(reports)
        self.assertEqual(decision.action, DecisionAction.NO_TRADE)
        self.assertEqual(len(decision.selected_evidence), 1)
        self.assertEqual(len(decision.discarded_as_correlated), 2)

    def test_two_independent_evidence_groups_can_produce_a_research_decision(self) -> None:
        decision = CentralTradingIntelligenceEngine().decide([
            report("quant", evidence("quant", "statistical_trend", .7)),
            report("volume", evidence("volume", "volume_confirmation", .6)),
        ])
        self.assertEqual(decision.action, DecisionAction.BUY)

    def test_risk_engine_stops_after_daily_loss_limit(self) -> None:
        levels = ProposedLevels(100, 99, (102,), "unit")
        result = RiskManagementEngine().assess(DecisionAction.BUY, levels, PortfolioState(10_000, realized_daily_loss=250), InstrumentRiskSpec("XAUUSD", 100, .01, .01))
        self.assertFalse(result.approved)
        self.assertIn("Daily loss limit", result.reasons[0])

    def test_risk_circuit_breakers_cover_revenge_and_event_risk(self) -> None:
        levels = ProposedLevels(100, 99, (102,), "unit")
        result = RiskManagementEngine().assess(
            DecisionAction.BUY, levels, PortfolioState(10_000, consecutive_losses=3, event_risk_active=True),
            InstrumentRiskSpec("XAUUSD", 100, .01, .01),
        )
        self.assertFalse(result.approved)
        self.assertTrue(any("Consecutive-loss" in reason for reason in result.reasons))
        self.assertTrue(any("event-risk" in reason for reason in result.reasons))

    def test_analysis_only_never_issues_trade_signal(self) -> None:
        decision = CentralTradingIntelligenceEngine().decide([
            report("quant", evidence("quant", "statistical_trend", .7)),
            report("volume", evidence("volume", "volume_confirmation", .6)),
        ])
        outcome = TradeSignalEngine(RiskManagementEngine(), OperatingMode.ANALYSIS_ONLY).build(
            decision, test_snapshot(), "H1", ProposedLevels(100, 99, (102,), "unit"), PortfolioState(10_000),
            InstrumentRiskSpec("XAUUSD", 100, .01, .01), ("quant.trend_baseline",),
        )
        self.assertEqual(outcome.signal.action, DecisionAction.NO_TRADE)
        self.assertEqual(outcome.signal.status, "BLOCKED")
        self.assertTrue(any("analysis-only" in reason for reason in outcome.reasons))
        self.assertIn("operating_mode.paper", {item.condition_id for item in outcome.signal.conditions})

    def test_verified_paper_signal_has_structured_conditions_and_non_probabilistic_confidence(self) -> None:
        decision = CentralTradingIntelligenceEngine().decide([
            report("quant", evidence("quant", "statistical_trend", .7)),
            report("volume", evidence("volume", "volume_confirmation", .6)),
        ])
        bar = Candle(NOW - timedelta(hours=1), NOW, 100, 101, 99, 100.5, 100)
        snapshot = MarketSnapshot(NOW, FeedMetadata("fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.PAPER, NOW, live_feed_verified=True), {"H1": (bar,)})
        outcome = TradeSignalEngine(RiskManagementEngine(), OperatingMode.PAPER).build(
            decision, snapshot, "H1", ProposedLevels(100, 99, (102,), "closed-bar reference"), PortfolioState(10_000),
            InstrumentRiskSpec("XAUUSD", 100, .01, .01), ("quant.trend_baseline", "volume.relative_volume"),
        )
        self.assertEqual(outcome.signal.action, DecisionAction.BUY)
        self.assertEqual(outcome.signal.status, "PAPER_CANDIDATE")
        self.assertIsNotNone(outcome.signal.levels)
        self.assertGreater(outcome.signal.risk.reward_to_risk or 0, 1)
        self.assertIn("not a probability", outcome.signal.confidence_meaning)
        states = {item.condition_id: item.state for item in outcome.signal.conditions}
        self.assertEqual(states["data.closed_candles"], "MET")
        self.assertEqual(states["entry.levels"], "MET")
        self.assertEqual(states["risk.independent_gate"], "MET")


if __name__ == "__main__":
    unittest.main()
