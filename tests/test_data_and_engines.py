from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.data import validate_closed_candles
from trading_intelligence.engines import ICTAnalysisEngine, PriceActionEngine, QuantitativeAnalysisEngine, VolumeAnalysisEngine
from trading_intelligence.models import Candle, DataMode, FeedMetadata, MarketSnapshot


def candles_from_closes(closes: list[float], volumes: list[float] | None = None) -> tuple[Candle, ...]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = []
    for i, close in enumerate(closes):
        opening = closes[i - 1] if i else close - 0.25
        result.append(Candle(start + timedelta(hours=i), start + timedelta(hours=i + 1), opening, max(opening, close) + .2, min(opening, close) - .2, close, volumes[i] if volumes else 100.0))
    return tuple(result)


def snapshot(candles: tuple[Candle, ...]) -> MarketSnapshot:
    now = candles[-1].close_time
    return MarketSnapshot(now, FeedMetadata("unit", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.SYNTHETIC, now), {"H1": candles})


class DataAndEngineTests(unittest.TestCase):
    def test_rejects_a_future_bar_to_prevent_lookahead(self) -> None:
        now = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        future = Candle(now, now + timedelta(hours=1), 1, 2, 1, 1.5, 1)
        report = validate_closed_candles((future,), now)
        self.assertFalse(report.passed)
        self.assertIn("unclosed/future bar", report.errors[0])

    def test_quant_engine_marks_a_simple_uptrend_on_closed_bars(self) -> None:
        bars = candles_from_closes([100 + i * .5 for i in range(70)])
        report = QuantitativeAnalysisEngine().analyze(snapshot(bars), "H1")
        self.assertEqual(report.proposed_direction.value, "BUY")
        self.assertEqual(report.evidence[0].correlation_group, "statistical_trend")

    def test_volume_engine_refuses_missing_volume(self) -> None:
        bars = candles_from_closes([100 + i * .2 for i in range(30)], [0.0] * 30)
        report = VolumeAnalysisEngine().analyze(snapshot(bars), "H1")
        self.assertEqual(report.proposed_direction.value, "FLAT")
        self.assertTrue(report.required_inputs_missing)

    def test_ict_never_emits_evidence_on_invalid_data(self) -> None:
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        invalid = Candle(now, now + timedelta(hours=1), 10, 11, 9, 10.5, 1)
        snap = MarketSnapshot(now, FeedMetadata("unit", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.SYNTHETIC, now), {"H1": (invalid,)})
        report = ICTAnalysisEngine().analyze(snap, "H1")
        self.assertEqual(report.proposed_direction.value, "FLAT")
        self.assertFalse(report.evidence)


if __name__ == "__main__":
    unittest.main()
