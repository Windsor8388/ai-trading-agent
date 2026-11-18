"""A local deterministic demo. It never downloads prices or connects to a broker."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from .central import CentralTradingIntelligenceEngine
from .engines import ICTAnalysisEngine, PriceActionEngine, QuantitativeAnalysisEngine, SMCAnalysisEngine, VolumeAnalysisEngine
from .models import Candle, DataMode, FeedMetadata, MarketSnapshot
from .orchestration import AnalysisOrchestrator


def _demo_snapshot() -> MarketSnapshot:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 2600.0
    for i in range(90):
        open_price = price
        price += 0.35 + (0.18 if i % 7 in (0, 1) else -0.10)
        high = max(open_price, price) + 0.4
        low = min(open_price, price) - 0.3
        candles.append(Candle(start + timedelta(hours=i), start + timedelta(hours=i + 1), open_price, high, low, price, 100 + i % 12))
    as_of = candles[-1].close_time
    feed = FeedMetadata("synthetic-demo", "XAUUSD", "XAUUSD_SPOT", "MID", DataMode.SYNTHETIC, as_of, quality_status="SYNTHETIC")
    return MarketSnapshot(as_of, feed, {"H1": tuple(candles)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline, non-tradable analysis demo.")
    parser.add_argument("--demo", action="store_true", help="Run deterministic synthetic OHLCV example")
    args = parser.parse_args()
    if not args.demo:
        parser.error("Only --demo is currently supported; no live-data connector is configured.")
    snapshot = _demo_snapshot()
    runner = AnalysisOrchestrator((PriceActionEngine(), ICTAnalysisEngine(), SMCAnalysisEngine(), VolumeAnalysisEngine(), QuantitativeAnalysisEngine()))
    run = runner.run(snapshot, ("H1",))
    decision = CentralTradingIntelligenceEngine().decide(run.reports)
    payload = {
        "data_mode": snapshot.feed.mode.value,
        "reports": [{"engine": item.engine, "direction": item.proposed_direction.value, "confidence": item.confidence, "observations": item.observations} for item in run.reports],
        "decision": {"action": decision.action.value, "confidence": decision.confidence, "reasons": decision.supporting_reasons, "discarded_correlated_count": len(decision.discarded_as_correlated)},
        "safety": "Synthetic offline data only; this output is not a market signal or an order.",
    }
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
