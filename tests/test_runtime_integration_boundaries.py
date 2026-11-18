from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.agents import ChartAnalysisAgent, MarketResearchAgent
from trading_intelligence.central import CentralTradingIntelligenceEngine
from trading_intelligence.engines.base import AnalysisEngine
from trading_intelligence.integrations import (
    BingXRuntimeBinding,
    BingXVenueContract,
    GeminiIntegrationPolicy,
    GeminiRole,
    IntegrationState,
    LiveIntegrationGate,
    LiveOperationApproval,
    SecretReference,
)
from trading_intelligence.models import (
    Candle,
    DataMode,
    Direction,
    EngineReport,
    Evidence,
    FeedMetadata,
    InstrumentRiskSpec,
    MarketRegime,
    MarketSnapshot,
    PortfolioState,
)
from trading_intelligence.orchestration import AnalysisOrchestrator
from trading_intelligence.risk import RiskManagementEngine
from trading_intelligence.signals import OperatingMode, TradeSignalEngine


NOW = datetime(2025, 2, 1, 12, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class StaticEvidenceEngine(AnalysisEngine):
    score: float = 0.7
    group: str = "group"

    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        evidence = Evidence(
            f"{self.name}-{timeframe}", self.name, timeframe, self.score, abs(self.score), self.group,
            "fixture measurement", "fixture independent evidence", "quant.trend_baseline",
        )
        return EngineReport(
            self.name, self.version, timeframe, snapshot.as_of,
            Direction.BUY if self.score > 0 else Direction.SELL, abs(self.score), MarketRegime.TRENDING_UP,
            evidence=(evidence,), invalidation_conditions=("fixture invalidation",),
        )


def snapshot() -> MarketSnapshot:
    candles = []
    for index in range(16):
        opened = NOW - timedelta(hours=16 - index)
        close = 100 + index * .2
        candles.append(Candle(opened, opened + timedelta(hours=1), close - .1, close + 1, close - 1, close, 10))
    return MarketSnapshot(
        NOW,
        FeedMetadata("approved_fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.PAPER, NOW, live_feed_verified=True),
        {"H1": tuple(candles)},
    )


class IntegrationBoundaryTests(unittest.TestCase):
    def test_secret_references_are_not_raw_keys_and_gemini_can_stay_disabled(self) -> None:
        with self.assertRaises(ValueError):
            SecretReference("api_key=not-allowed")
        readiness = LiveIntegrationGate.gemini_readiness(GeminiIntegrationPolicy(GeminiRole.DISABLED))
        self.assertEqual(readiness.state, IntegrationState.DISABLED)

    def test_bingx_readiness_requires_contract_binding_drills_and_time_bounded_approval(self) -> None:
        disconnected = LiveIntegrationGate.bingx_readiness(None, None, now=NOW)
        self.assertEqual(disconnected.state, IntegrationState.AWAITING_SECRET_BINDING)

        contract = BingXVenueContract(
            documentation_url="https://docs.example.test/bingx-reviewed", documentation_version="review-2026-02",
            api_base_url="https://gateway.example.test", market_data_adapter_version="1.0", execution_adapter_version="1.0",
            account_scope="SPOT", client_order_id_supported=True, order_status_reconciliation_supported=True,
            cancel_supported=True, websocket_recovery_supported=True,
        )
        binding = BingXRuntimeBinding(SecretReference("vault://trading/bingx/api-key"), SecretReference("vault://trading/bingx/api-secret"), contract, True)
        forward = LiveIntegrationGate.bingx_readiness(binding, None, now=NOW)
        self.assertEqual(forward.state, IntegrationState.FORWARD_PAPER_READY)
        approval = LiveOperationApproval(
            "live-approval-001", "risk-officer", NOW - timedelta(minutes=5), NOW + timedelta(days=1), ("BTCUSD",),
            100, 50, True, True, True,
        )
        ready = LiveIntegrationGate.bingx_readiness(binding, approval, now=NOW)
        self.assertEqual(ready.state, IntegrationState.LIVE_BLOCKED)
        self.assertIn("no order sender", ready.safe_summary)

    def test_agent_coordinates_independent_evidence_and_only_emits_paper_candidate(self) -> None:
        risk = RiskManagementEngine()
        agent = MarketResearchAgent(
            AnalysisOrchestrator((
                StaticEvidenceEngine("quant_fixture", "test", .7, "statistical_trend"),
                StaticEvidenceEngine("volume_fixture", "test", .6, "volume_confirmation"),
            )),
            CentralTradingIntelligenceEngine(), risk, TradeSignalEngine(risk, OperatingMode.PAPER),
        )
        result = agent.assess(
            snapshot(), ("H1",), signal_timeframe="H1", portfolio=PortfolioState(10_000),
            instrument_spec=InstrumentRiskSpec("XAUUSD", 1, 1, 1), strategy_ids=("strategy.quant_trend_baseline",),
        )
        self.assertEqual(result.decision.action.value, "BUY")
        self.assertIsNotNone(result.levels)
        self.assertEqual(result.signal_result.signal.status, "PAPER_CANDIDATE")
        self.assertGreater(result.signal_result.signal.risk.reward_to_risk or 0, 1)
        chart_result = ChartAnalysisAgent(agent).assess_chart(
            snapshot(), ("H1",), signal_timeframe="H1", portfolio=PortfolioState(10_000),
            instrument_spec=InstrumentRiskSpec("XAUUSD", 1, 1, 1), strategy_ids=("strategy.quant_trend_baseline",),
        )
        self.assertEqual(chart_result.assessment.signal_result.signal.status, "PAPER_CANDIDATE")
        self.assertIsNone(chart_result.gemini_advisory)


if __name__ == "__main__":
    unittest.main()
