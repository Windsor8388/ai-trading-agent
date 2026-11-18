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


if __name__ == "__main__":
    unittest.main()
