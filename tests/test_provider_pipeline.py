from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
import unittest

from trading_intelligence.models import Candle, DataMode, FeedMetadata
from trading_intelligence.providers import (
    BarRequest,
    DataPipelinePolicy,
    MarketDataPipeline,
    MarketDataResponse,
    OandaV20Config,
    OandaV20MarketDataProvider,
    PeriodicPollingCoordinator,
    inspect_gaps_and_anomalies,
)


NOW = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)
REQUEST = BarRequest("XAUUSD", "H1", NOW - timedelta(hours=3), NOW, "MID")


class FixtureTransport:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Mapping[str, str], float]] = []

    def get_json(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        self.calls.append((url, headers, timeout_seconds))
        return self.payload


class FixtureProvider:
    name = "fixture_provider"

    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles
        self.calls = 0

    def fetch_completed_bars(self, request: BarRequest) -> MarketDataResponse:
        self.calls += 1
        return MarketDataResponse(
            self.candles,
            FeedMetadata(self.name, request.instrument, "XAUUSD_SPOT", request.price_component, DataMode.PAPER, NOW, live_feed_verified=True),
            f"fixture-{self.calls}", "a" * 64,
        )


def candle(opened: datetime, low: float = 99, high: float = 101) -> Candle:
    return Candle(opened, opened + timedelta(hours=1), 100, high, low, 100.5, 10)


class ProviderPipelineTests(unittest.TestCase):
    def test_oanda_adapter_parses_only_completed_bars_and_preserves_lineage(self) -> None:
        transport = FixtureTransport({
            "requestID": "request-unit",
            "candles": [
                {"complete": True, "time": "2025-01-02T09:00:00Z", "volume": 7, "mid": {"o": "100", "h": "102", "l": "99", "c": "101"}},
                {"complete": True, "time": "2025-01-02T10:00:00Z", "volume": 8, "mid": {"o": "101", "h": "103", "l": "100", "c": "102"}},
                {"complete": False, "time": "2025-01-02T11:00:00Z", "volume": 1, "mid": {"o": "102", "h": "104", "l": "101", "c": "103"}},
            ],
        })
        provider = OandaV20MarketDataProvider(
            OandaV20Config(
                access_token="approved-test-token",
                instrument_map={"XAUUSD": "XAU_USD"},
                instrument_kinds={"XAUUSD": "XAUUSD_SPOT"},
                verified_read_only_connection=False,
            ),
            transport,
        )
        response = provider.fetch_completed_bars(REQUEST)
        self.assertEqual(len(response.candles), 2)
        self.assertEqual(response.dropped_incomplete_bars, 1)
        self.assertEqual(response.feed.vendor_symbol, "XAU_USD")
        self.assertFalse(response.feed.live_feed_verified)
        self.assertEqual(response.request_id, "request-unit")
        self.assertIn("granularity=H1", transport.calls[0][0])
        self.assertIn("price=M", transport.calls[0][0])
        self.assertNotIn("approved-test-token", response.feed.source_url or "")

    def test_pipeline_quarantines_gaps_but_keeps_receipt_and_does_not_fill_them(self) -> None:
        provider = FixtureProvider((candle(NOW - timedelta(hours=3)), candle(NOW - timedelta(hours=1))))
        pipeline = MarketDataPipeline(DataPipelinePolicy(max_missing_bars=0))
        receipt = pipeline.ingest(provider, REQUEST, observed_at=NOW)
        self.assertFalse(receipt.accepted)
        self.assertIsNotNone(receipt.quality)
        assert receipt.quality is not None
        self.assertEqual(receipt.quality.gaps[0].missing_bars, 1)
        self.assertIn("Missing-bar count", receipt.quality.reasons[0])
        self.assertIsNone(pipeline.latest_accepted(provider.name, "XAUUSD", "H1", "MID"))
        self.assertEqual(len(receipt.response.candles if receipt.response else ()), 2)

    def test_anomaly_classifier_and_explicit_periodic_polling_are_deterministic(self) -> None:
        bars = tuple(candle(NOW - timedelta(hours=22 - index), 99, 101) for index in range(21))
        anomalous = (*bars, candle(NOW - timedelta(hours=1), 80, 120))
        gaps, anomalies = inspect_gaps_and_anomalies(anomalous, "H1", range_multiple=5)
        self.assertEqual(gaps, ())
        self.assertEqual(anomalies[-1].category, "RANGE_OUTLIER")

        provider = FixtureProvider((candle(NOW - timedelta(hours=2)), candle(NOW - timedelta(hours=1))))
        pipeline = MarketDataPipeline()
        coordinator = PeriodicPollingCoordinator(pipeline)
        coordinator.register("xau-h1", provider, lambda now: BarRequest("XAUUSD", "H1", now - timedelta(hours=2), now, "MID"), cadence=timedelta(minutes=5), first_due_at=NOW)
        first = coordinator.poll_due(NOW)
        second = coordinator.poll_due(NOW + timedelta(minutes=2))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        self.assertEqual(provider.calls, 1)
        self.assertEqual(coordinator.schedules()[0].next_due_at, NOW + timedelta(minutes=5))


if __name__ == "__main__":
    unittest.main()
