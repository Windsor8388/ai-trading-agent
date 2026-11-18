from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.engines import SessionContextEngine, StructureEventEngine, VolumeProfileAvailabilityEngine
from trading_intelligence.events import EconomicEventRiskGate, EventRiskPolicy
from trading_intelligence.models import Candle, DataMode, FeedMetadata, MarketSnapshot
from trading_intelligence.providers.contracts import EconomicEvent


NOW = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)


def market_snapshot() -> MarketSnapshot:
    highs = [101, 102, 103, 105, 103, 102, 102, 103, 105, 108, 110, 108, 106, 107, 106, 107, 109, 111, 112, 113]
    lows = [99, 98, 97, 98, 97, 96, 95, 96, 98, 99, 100, 102, 101, 100, 101, 102, 103, 104, 105, 106]
    candles = []
    start = NOW - timedelta(hours=len(highs))
    for index, (high, low) in enumerate(zip(highs, lows)):
        opened = start + timedelta(hours=index)
        opened_price = (high + low) / 2
        close = 111 if index == len(highs) - 1 else opened_price
        candles.append(Candle(opened, opened + timedelta(hours=1), opened_price, high, low, close, 10))
    return MarketSnapshot(NOW, FeedMetadata("fixture", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.HISTORICAL, NOW), {"H1": tuple(candles)})


class MarketContextAndEventTests(unittest.TestCase):
    def test_structure_engine_labels_closed_bar_bos_and_session_is_context_only(self) -> None:
        snapshot = market_snapshot()
        structure = StructureEventEngine().analyze(snapshot, "H1")
        session = SessionContextEngine().analyze(snapshot, "H1")
        self.assertEqual(structure.proposed_direction.value, "BUY")
        self.assertIn("BOS_BULLISH", structure.evidence[0].measurement)
        self.assertEqual(structure.evidence[0].correlation_group, "market_structure")
        self.assertEqual(session.evidence, ())
        self.assertIn("not an exchange calendar", session.observations[0])

    def test_volume_profile_engine_refuses_to_invent_vap_from_ohlcv(self) -> None:
        report = VolumeProfileAvailabilityEngine().analyze(market_snapshot(), "H1")
        self.assertEqual(report.evidence, ())
        self.assertIn("volume-at-price", report.required_inputs_missing[0])

    def test_high_impact_calendar_event_activates_a_time_bounded_risk_embargo(self) -> None:
        event = EconomicEvent("US-CPI", "US", "HIGH", NOW + timedelta(minutes=10), None, None, None, None, None, "https://calendar.example/events/US-CPI")
        gate = EconomicEventRiskGate(EventRiskPolicy(embargo_before=timedelta(minutes=30), embargo_after=timedelta(minutes=15)))
        active = gate.assess((event,), NOW)
        after = gate.assess((event,), NOW + timedelta(hours=1))
        self.assertTrue(active.active)
        self.assertIn("US-CPI", active.reasons[0])
        self.assertFalse(after.active)


if __name__ == "__main__":
    unittest.main()
