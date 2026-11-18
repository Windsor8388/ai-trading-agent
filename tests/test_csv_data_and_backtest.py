from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_intelligence.backtest import BacktestConfig, BacktestEngine, BacktestInstruction
from trading_intelligence.data import resample_intraday_bars
from trading_intelligence.models import Candle, DataMode, DecisionAction, FeedMetadata, MarketSnapshot, ProposedLevels
from trading_intelligence.providers import CSVBarSpec, import_ohlcv_csv


START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def bar(index: int, opening: float, high: float, low: float, close: float) -> Candle:
    return Candle(START + timedelta(hours=index), START + timedelta(hours=index + 1), opening, high, low, close, 10 + index)


def feed() -> FeedMetadata:
    return FeedMetadata("unit", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.HISTORICAL, START, quality_status="PASS")


class CSVDataAndBacktestTests(unittest.TestCase):
    def test_csv_import_is_fingerprinted_and_normalized_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            path.write_text("timestamp,open,high,low,close,volume\n2025-01-01T03:00:00+03:00,100,102,99,101,12\n2025-01-01T04:00:00+03:00,101,103,100,102,13\n", encoding="utf-8")
            result = import_ohlcv_csv(CSVBarSpec(path, "H1", "fixture", "XAUUSD", "XAUUSD_SPOT", "MID"), retrieved_at=START)
        self.assertEqual(result.manifest.rows, 2)
        self.assertEqual(result.candles[0].open_time, START)
        self.assertEqual(len(result.manifest.dataset_sha256), 64)
        self.assertEqual(result.manifest.feed.mode, DataMode.HISTORICAL)

    def test_resampler_drops_incomplete_target_bucket_instead_of_filling_gap(self) -> None:
        hourly = tuple(bar(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(5))
        four_hour = resample_intraday_bars(hourly, "H1", "H4")
        self.assertEqual(len(four_hour), 1)
        self.assertEqual(four_hour[0].open, hourly[0].open)
        self.assertEqual(four_hour[0].close, hourly[3].close)
        self.assertEqual(four_hour[0].volume, sum(item.volume for item in hourly[:4]))

    def test_signal_is_visible_only_after_close_and_enters_next_open(self) -> None:
        candles = (bar(0, 100, 101, 99, 100), bar(1, 101, 105, 100, 104), bar(2, 104, 105, 103, 104))
        seen: list[MarketSnapshot] = []

        def strategy(snapshot: MarketSnapshot) -> BacktestInstruction | None:
            seen.append(snapshot)
            if len(snapshot.candles("H1")) == 1:
                return BacktestInstruction(DecisionAction.BUY, ProposedLevels(100, 98, (104,), "closed-bar unit signal"), "unit.buy", .6)
            return None

        result = BacktestEngine(BacktestConfig(10_000, 1, 1, 252)).run(candles, feed(), "H1", strategy)
        self.assertEqual(result.summary.trade_count, 1)
        self.assertEqual(result.trades[0].entry_price, 101)
        self.assertEqual(result.trades[0].exit_reason, "TAKE_PROFIT")
        self.assertEqual(seen[0].as_of, candles[0].close_time)
        self.assertTrue(all(item.candles("H1")[-1].close_time <= item.as_of for item in seen))

    def test_ambiguous_stop_and_target_bar_is_scored_stop_first(self) -> None:
        candles = (bar(0, 100, 101, 99, 100), bar(1, 100, 103, 97, 100), bar(2, 100, 101, 99, 100))

        def strategy(snapshot: MarketSnapshot) -> BacktestInstruction | None:
            return BacktestInstruction(DecisionAction.BUY, ProposedLevels(100, 98, (102,), "unit"), "unit.ambiguous") if len(snapshot.candles("H1")) == 1 else None

        result = BacktestEngine(BacktestConfig(10_000, 1, 1, 252)).run(candles, feed(), "H1", strategy)
        self.assertEqual(result.trades[0].exit_reason, "STOP_FIRST_AMBIGUOUS")
        self.assertEqual(result.trades[0].exit_price, 98)

    def test_backtest_refuses_live_feed_metadata(self) -> None:
        candles = (bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100))
        with self.assertRaisesRegex(ValueError, "never a live feed"):
            BacktestEngine(BacktestConfig(10_000, 1, 1, 252)).run(candles, replace(feed(), mode=DataMode.LIVE), "H1", lambda snapshot: None)


if __name__ == "__main__":
    unittest.main()
