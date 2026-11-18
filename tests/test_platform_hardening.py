from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading_intelligence.engines import LiquidityProxyEngine, default_research_engines, engine_catalog
from trading_intelligence.execution import ExecutionMode, ExecutionPolicy, OrderLifecycleManager, OrderStatus
from trading_intelligence.knowledge import KnowledgeBase
from trading_intelligence.models import (
    Candle,
    DataMode,
    DecisionAction,
    FeedMetadata,
    InstrumentRiskSpec,
    MarketSnapshot,
    ProposedLevels,
    RiskAssessment,
    TradeSignal,
)
from trading_intelligence.orchestration import AnalysisOrchestrator
from trading_intelligence.providers import (
    AssetClass,
    BarRequest,
    DataPipelinePolicy,
    MarketDataPipeline,
    MarketDataResponse,
    default_instrument_registry,
)
from trading_intelligence.research.ledger import ResearchLedger


NOW = datetime(2025, 1, 5, 12, tzinfo=timezone.utc)
REQUEST = BarRequest("XAUUSD", "H1", NOW - timedelta(hours=2), NOW, "MID")


def bar(opened: datetime, *, high: float = 101, low: float = 99, close: float = 100) -> Candle:
    return Candle(opened, opened + timedelta(hours=1), (high + low) / 2, high, low, close, 10)


def paper_signal(*, entry: float = 100, quantity: float = 1) -> TradeSignal:
    return TradeSignal(
        instrument="XAUUSD", action=DecisionAction.BUY, timeframe="H1", strategy_ids=("hardening",),
        levels=ProposedLevels(entry, entry - 1, (entry + 2,), "fixture"), confidence=.6,
        data_source="fixture", created_at=NOW,
        risk=RiskAssessment(True, quantity, 1, ("fixture",), 2, 1, 1),
        supporting_evidence=(), opposing_reasons=(), invalidation_conditions=(), status="PAPER_CANDIDATE",
    )


class ReplayProvider:
    name = "replay_provider"

    def __init__(self, candles: tuple[Candle, ...], *, mode: DataMode = DataMode.PAPER) -> None:
        self.candles = candles
        self.mode = mode
        self.calls = 0

    def fetch_completed_bars(self, request: BarRequest) -> MarketDataResponse:
        self.calls += 1
        feed = FeedMetadata(
            self.name, request.instrument, "XAUUSD_SPOT", request.price_component, self.mode, NOW,
            vendor_symbol="XAU_USD", quality_status="PASS",
        )
        return MarketDataResponse(self.candles, feed, f"request-{self.calls}", f"{self.calls:064x}")


class ExecutionHardeningTests(unittest.TestCase):
    def test_client_id_cannot_be_reused_for_different_intent_and_active_limits_are_hard(self) -> None:
        policy = ExecutionPolicy(
            {"XAUUSD": InstrumentRiskSpec("XAUUSD", 1, 1, 1)},
            max_order_notional=150, max_order_cash_risk=10, max_active_orders=1, max_gross_open_notional=150,
        )
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=policy)
        first = manager.prepare(paper_signal(), "dedupe-1", NOW)
        self.assertEqual(first.status, OrderStatus.VALIDATED)
        self.assertIs(first, manager.prepare(paper_signal(), "dedupe-1", NOW))
        with self.assertRaisesRegex(ValueError, "different immutable order intent"):
            manager.prepare(replace(paper_signal(), levels=ProposedLevels(101, 100, (103,), "changed")), "dedupe-1", NOW)
        blocked = manager.prepare(paper_signal(), "dedupe-2", NOW)
        self.assertEqual(blocked.status, OrderStatus.REJECTED)
        self.assertIn("Active-order count", blocked.rejection_reason or "")

        gross_policy = ExecutionPolicy(
            {"XAUUSD": InstrumentRiskSpec("XAUUSD", 1, 1, 1)},
            max_order_cash_risk=10, max_active_orders=3, max_gross_open_notional=150,
        )
        gross_manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=gross_policy)
        gross_manager.prepare(paper_signal(entry=100), "gross-1", NOW)
        gross_blocked = gross_manager.prepare(paper_signal(entry=60), "gross-2", NOW)
        self.assertEqual(gross_blocked.status, OrderStatus.REJECTED)
        self.assertIn("Gross open notional", gross_blocked.rejection_reason or "")

    def test_duplicate_venue_events_do_not_double_transition_or_fill(self) -> None:
        policy = ExecutionPolicy({"XAUUSD": InstrumentRiskSpec("XAUUSD", 1, 1, 1)}, max_order_cash_risk=10)
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=policy)
        manager.prepare(paper_signal(), "venue-idempotent", NOW)
        submitted = manager.record_submitted("venue-idempotent", "venue-1", NOW + timedelta(seconds=1))
        self.assertIs(submitted, manager.record_submitted("venue-idempotent", "venue-1", NOW + timedelta(seconds=2)))
        manager.record_acknowledged("venue-idempotent", NOW + timedelta(seconds=3))
        partial = manager.record_fill("venue-idempotent", .5, NOW + timedelta(seconds=4))
        self.assertIs(partial, manager.record_fill("venue-idempotent", .5, NOW + timedelta(seconds=5)))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            manager.record_submitted("venue-idempotent", "venue-other", NOW + timedelta(seconds=6))


class KnowledgeAndEngineCompletionTests(unittest.TestCase):
    def test_structured_knowledge_retrieval_exposes_validity_failures_and_sources(self) -> None:
        base = KnowledgeBase.from_directory()
        concept = base.get("ict.fair_value_gap")
        self.assertEqual(base.version, "1.1")
        self.assertTrue(concept.use_cases)
        self.assertTrue(concept.examples)
        self.assertTrue(concept.common_failures)
        self.assertIn("point-in-time", concept.signal_validity)
        self.assertTrue(base.sources_for(concept.concept_id)[0].source_version)
        retrieved = base.retrieve("fair value gap", school="ICT", requires_all=("closed OHLC",))
        self.assertEqual(retrieved[0].concept.concept_id, "ict.fair_value_gap")
        self.assertIn("identity", retrieved[0].matched_fields)

    def test_catalog_has_explicit_liquidity_session_and_ml_reports(self) -> None:
        descriptors = {descriptor.engine_id for descriptor in engine_catalog()}
        engine_names = {engine.name for engine in default_research_engines()}
        self.assertTrue({"liquidity", "session_context", "ml"}.issubset(descriptors))
        self.assertTrue({"liquidity", "session_context", "ml"}.issubset(engine_names))

    def test_liquidity_proxy_identifies_completed_equal_high_sweep_without_l2_claim(self) -> None:
        highs = (101, 103, 105, 107, 110, 106, 104, 103, 104, 107, 110.05, 106, 104, 103, 104, 105, 106, 107, 108, 112)
        candles = tuple(
            bar(NOW - timedelta(hours=len(highs) - index), high=high, low=high - 4, close=109.5 if index == len(highs) - 1 else high - 2)
            for index, high in enumerate(highs)
        )
        snapshot = MarketSnapshot(NOW, FeedMetadata("fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.HISTORICAL, NOW), {"H1": candles})
        report = LiquidityProxyEngine().analyze(snapshot, "H1")
        self.assertEqual(report.proposed_direction.value, "SELL")
        self.assertEqual(report.evidence[0].source_concept_id, "liquidity.stop_cluster_proxy")
        self.assertIn("L2/order-book", report.required_inputs_missing[0])

    def test_orchestrator_records_each_independent_report_in_hash_ledger(self) -> None:
        snapshot = MarketSnapshot(
            NOW,
            FeedMetadata("fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.HISTORICAL, NOW),
            {"H1": (bar(NOW - timedelta(hours=1)),)},
        )
        engine = next(engine for engine in default_research_engines() if engine.name == "ml")
        with TemporaryDirectory() as temporary_directory:
            ledger = ResearchLedger(Path(temporary_directory) / "analysis.jsonl")
            run = AnalysisOrchestrator((engine,)).run_and_record(snapshot, ("H1",), ledger=ledger, run_id="run-001")
            entries = ledger.verify()
        self.assertEqual(run.reports[0].engine, "ml")
        self.assertEqual(entries[0].category, "analysis.engine_report")
        self.assertEqual(entries[0].payload["report"]["proposed_direction"], "FLAT")


class DataPipelineCompletionTests(unittest.TestCase):
    def test_registry_declares_supported_asset_classes_without_vendor_substitution(self) -> None:
        registry = default_instrument_registry()
        self.assertEqual(registry.get("XAUUSD").asset_class, AssetClass.METAL_SPOT)
        self.assertEqual(registry.get("EURUSD").asset_class, AssetClass.FX_SPOT)
        self.assertEqual(registry.get("US500").asset_class, AssetClass.INDEX_CASH)
        self.assertEqual(registry.get("AAPL").asset_class, AssetClass.EQUITY)
        self.assertEqual(registry.get("BTCUSD").asset_class, AssetClass.CRYPTO_SPOT)
        self.assertNotEqual(registry.get("XAUUSD").instrument_kind, registry.get("GC").instrument_kind)

    def test_pipeline_records_replay_quarantines_conflict_and_builds_quality_gated_snapshot(self) -> None:
        bars = (bar(NOW - timedelta(hours=2)), bar(NOW - timedelta(hours=1)))
        provider = ReplayProvider(bars)
        pipeline = MarketDataPipeline(instrument_registry=default_instrument_registry())
        first = pipeline.ingest(provider, REQUEST, observed_at=NOW)
        replay = pipeline.ingest(provider, REQUEST, observed_at=NOW)
        self.assertTrue(first.accepted)
        self.assertTrue(replay.accepted)
        assert replay.quality is not None
        self.assertEqual(replay.quality.duplicates[0].category, "EXACT_REPLAY")
        self.assertEqual(len(pipeline.normalized_bars(provider.name, "XAUUSD", "H1", "MID")), 2)
        snapshot = pipeline.build_snapshot(provider.name, "XAUUSD", "MID", ("H1",), as_of=NOW)
        self.assertEqual(len(snapshot.candles("H1")), 2)

        provider.candles = (bar(NOW - timedelta(hours=2), close=100.5), bar(NOW - timedelta(hours=1)))
        conflict = pipeline.ingest(provider, REQUEST, observed_at=NOW)
        self.assertFalse(conflict.accepted)
        assert conflict.quality is not None
        self.assertIn("Conflicting provider revisions", conflict.quality.reasons[0])

    def test_live_staleness_is_a_quality_blocker_not_a_silent_reuse(self) -> None:
        provider = ReplayProvider((bar(NOW - timedelta(hours=2)),), mode=DataMode.LIVE)
        pipeline = MarketDataPipeline(DataPipelinePolicy(max_live_staleness=timedelta(minutes=5)))
        receipt = pipeline.ingest(provider, REQUEST, observed_at=NOW)
        self.assertFalse(receipt.accepted)
        assert receipt.quality is not None
        self.assertGreater(receipt.quality.freshness_seconds or 0, 300)
        self.assertIn("stale", receipt.quality.reasons[0])


if __name__ == "__main__":
    unittest.main()
