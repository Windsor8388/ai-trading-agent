from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.engines import (
    ClassicalPatternAvailabilityEngine,
    EngineAvailability,
    FibonacciAnalysisEngine,
    FundamentalEventEngine,
    MarketProfileAvailabilityEngine,
    WyckoffAnalysisEngine,
    default_research_engines,
    engine_catalog,
)
from trading_intelligence.knowledge import KnowledgeBase
from trading_intelligence.models import Candle, DataMode, FeedMetadata, MarketSnapshot


NOW = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)


def snapshot(candles: tuple[Candle, ...], events=()) -> MarketSnapshot:
    return MarketSnapshot(NOW, FeedMetadata("fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.HISTORICAL, NOW), {"H1": candles}, economic_events=events)


def range_with_spring() -> tuple[Candle, ...]:
    bars = []
    start = NOW - timedelta(hours=21)
    for index in range(20):
        opened = start + timedelta(hours=index)
        bars.append(Candle(opened, opened + timedelta(hours=1), 100, 102, 98, 100, 10))
    opened = start + timedelta(hours=20)
    bars.append(Candle(opened, opened + timedelta(hours=1), 99, 101, 97, 99, 10))
    return tuple(bars)


def fibonacci_fixture() -> tuple[Candle, ...]:
    ranges = [(102, 98), (103, 98), (105, 99), (108, 101), (105, 100), (103, 99), (104, 98), (106, 100), (109, 102), (112, 104), (115, 106), (112, 105), (110, 103), (108, 101), (109, 102), (111, 104), (114, 106), (120, 108), (115, 106), (112, 105), (111, 104), (112, 105), (113, 106), (114, 107), (115, 108)]
    start = NOW - timedelta(hours=len(ranges))
    bars = []
    for index, (high, low) in enumerate(ranges):
        opened = (high + low) / 2
        close = 112.5 if index == len(ranges) - 1 else opened
        at = start + timedelta(hours=index)
        bars.append(Candle(at, at + timedelta(hours=1), opened, high, low, close, 10))
    return tuple(bars)


class AdvancedEngineTests(unittest.TestCase):
    def test_versioned_engine_catalog_maps_schools_to_capabilities_and_boundaries(self) -> None:
        catalog = engine_catalog()
        self.assertEqual(len({item.engine_id for item in catalog}), len(catalog))
        self.assertTrue(any(item.school == "Wyckoff" and item.availability == EngineAvailability.IMPLEMENTED for item in catalog))
        self.assertTrue(any(item.engine_id == "order_flow" and item.availability == EngineAvailability.DATA_REQUIRED for item in catalog))
        self.assertTrue(any(engine.name == "order_flow" for engine in default_research_engines()))
        knowledge = KnowledgeBase.from_directory()
        for descriptor in catalog:
            for concept_id in descriptor.concept_ids:
                self.assertEqual(knowledge.get(concept_id).concept_id, concept_id)

    def test_wyckoff_engine_reports_closed_bar_spring_candidate_without_phase_claim(self) -> None:
        report = WyckoffAnalysisEngine().analyze(snapshot(range_with_spring()), "H1")
        self.assertEqual(report.proposed_direction.value, "BUY")
        self.assertIn("SPRING_CANDIDATE", report.evidence[0].evidence_id)
        self.assertIn("not a confirmed accumulation", report.evidence[0].detail)
        self.assertEqual(report.evidence[0].correlation_group, "market_structure")

    def test_fibonacci_engine_uses_confirmed_opposite_swing_anchors(self) -> None:
        report = FibonacciAnalysisEngine().analyze(snapshot(fibonacci_fixture()), "H1")
        self.assertEqual(report.proposed_direction.value, "SELL")
        self.assertIn("nearest_ratio", report.evidence[0].measurement)
        self.assertEqual(report.evidence[0].source_concept_id, "fibonacci.retracement")
        self.assertEqual(report.evidence[0].correlation_group, "market_structure")

    def test_fundamental_engine_requires_calendar_and_only_reports_event_risk_context(self) -> None:
        missing = FundamentalEventEngine().analyze(snapshot(range_with_spring()), "H1")
        event = {"event_id": "US-CPI", "importance": "HIGH", "scheduled_at_utc": NOW + timedelta(minutes=10)}
        nearby = FundamentalEventEngine().analyze(snapshot(range_with_spring(), (event,)), "H1")
        self.assertIn("economic calendar", missing.required_inputs_missing[0])
        self.assertEqual(nearby.evidence, ())
        self.assertIn("US-CPI", nearby.observations[0])

    def test_unavailable_profile_and_classical_pattern_engines_do_not_fabricate_analysis(self) -> None:
        market_profile = MarketProfileAvailabilityEngine().analyze(snapshot(range_with_spring()), "H1")
        classical = ClassicalPatternAvailabilityEngine().analyze(snapshot(range_with_spring()), "H1")
        self.assertEqual(market_profile.evidence, ())
        self.assertIn("TPO", market_profile.required_inputs_missing[0])
        self.assertEqual(classical.evidence, ())
        self.assertIn("geometry", classical.required_inputs_missing[0])


if __name__ == "__main__":
    unittest.main()
