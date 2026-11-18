from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
import unittest

from trading_intelligence.advisors.gemini import GeminiChartAnalysisConfig, GeminiChartAnalyst
from trading_intelligence.central import CentralTradingIntelligenceEngine
from trading_intelligence.execution.bingx_swap import (
    BingXCredentials,
    BingXLiveDispatchDisabled,
    BingXSwapExecutionConfig,
    BingXSwapOrder,
    BingXSwapTestOrderClient,
    canonical_parameter_string,
)
from trading_intelligence.integrations import GeminiIntegrationPolicy, GeminiRole, SecretReference
from trading_intelligence.models import Candle, DataMode, Direction, EngineReport, Evidence, FeedMetadata, MarketRegime, MarketSnapshot
from trading_intelligence.providers.bingx_swap import BingXEnvironment, BingXSwapMarketConfig, BingXSwapMarketDataProvider
from trading_intelligence.providers.contracts import BarRequest
from trading_intelligence.runtime.secrets import EnvironmentSecretResolver, StaticSecretResolver


NOW = datetime(2025, 3, 1, 12, tzinfo=timezone.utc)


class FixturePublicTransport:
    def __init__(self, payload: dict, fail_primary: bool = False) -> None:
        self.payload = payload
        self.fail_primary = fail_primary
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, headers: dict[str, str], timeout_seconds: float) -> dict:
        self.calls.append((url, headers))
        if self.fail_primary and ".bingx.com" in url:
            raise URLError("fixture primary network failure")
        return self.payload


class FixtureSignedTransport:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"code": 0, "msg": "", "data": {"clientOrderId": "agent_1"}}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request_json(self, method: str, url: str, headers: dict[str, str], timeout_seconds: float) -> dict:
        self.calls.append((method, url, headers))
        return self.payload


class FixtureGeminiTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def generate_text(self, *, model_id: str, api_key: str, contents: str) -> str:
        self.calls.append({"model_id": model_id, "api_key": api_key, "contents": contents})
        return "ملاحظة بحثية: الاتجاه يحتاج تأكيداً مستقلاً وبيانات إضافية."


def snapshot() -> tuple[MarketSnapshot, EngineReport]:
    candles = []
    for index in range(16):
        opened = NOW - timedelta(hours=16 - index)
        close = 100 + index * 0.25
        candles.append(Candle(opened, opened + timedelta(hours=1), close - .1, close + 1, close - 1, close, 10))
    evidence = Evidence("fixture", "fixture_engine", "H1", .6, .6, "independent", "fixture", "fixture", "quant.trend_baseline")
    report = EngineReport("fixture_engine", "test", "H1", NOW, Direction.BUY, .6, MarketRegime.TRENDING_UP, (evidence,))
    market = MarketSnapshot(
        NOW,
        FeedMetadata("fixture", "BTCUSDT_PERP", "CRYPTO_PERPETUAL", "LAST", DataMode.PAPER, NOW, "BTC-USDT", live_feed_verified=True),
        {"H1": tuple(candles)},
    )
    return market, report


class BingXAndGeminiIntegrationTests(unittest.TestCase):
    def test_bingx_public_ohlcv_drops_no_data_and_preserves_official_lineage(self) -> None:
        start = datetime(2025, 3, 1, tzinfo=timezone.utc)
        request = BarRequest("BTCUSDT_PERP", "M1", start, start + timedelta(minutes=3), "LAST")
        payload = {
            "code": 0,
            "msg": "",
            "data": [
                {"time": int((start + timedelta(minutes=0)).timestamp() * 1000), "open": "10", "high": "12", "low": "9", "close": "11", "volume": "5"},
                {"time": int((start + timedelta(minutes=1)).timestamp() * 1000), "open": "11", "high": "13", "low": "10", "close": "12", "volume": "6"},
                {"time": int((start + timedelta(minutes=2)).timestamp() * 1000), "open": "12", "high": "14", "low": "11", "close": "13", "volume": "7"},
            ],
        }
        transport = FixturePublicTransport(payload, fail_primary=True)
        provider = BingXSwapMarketDataProvider(
            BingXSwapMarketConfig(BingXEnvironment.PROD_VST, {"BTCUSDT_PERP": "BTC-USDT"}, {"BTCUSDT_PERP": "CRYPTO_PERPETUAL"}, True),
            transport,
        )
        response = provider.fetch_completed_bars(request)
        self.assertEqual(len(response.candles), 3)
        self.assertEqual(response.feed.mode, DataMode.PAPER)
        self.assertTrue(response.feed.live_feed_verified)
        self.assertIn("docs=5d96e725", response.feed.notes)
        self.assertEqual(len(transport.calls), 2)
        self.assertIn("open-api-vst.bingx.pro", transport.calls[-1][0])
        self.assertEqual(transport.calls[-1][1]["X-SOURCE-KEY"], "BX-AI-SKILL")
        with self.assertRaises(ValueError):
            provider.fetch_completed_bars(BarRequest("BTCUSDT_PERP", "M1", start, start + timedelta(minutes=3), "MID"))

    def test_bingx_vst_test_order_is_signed_server_side_and_live_dispatch_is_refused(self) -> None:
        credentials = BingXCredentials(SecretReference("sm://env/BINGX_API_KEY"), SecretReference("sm://env/BINGX_SECRET_KEY"))
        parsed_config = BingXSwapExecutionConfig.from_environment({
            "BINGX_ENVIRONMENT": "prod-vst", "BINGX_API_KEY_REF": "sm://env/BINGX_API_KEY",
            "BINGX_API_SECRET_REF": "sm://env/BINGX_SECRET_KEY", "BINGX_RECV_WINDOW_MS": "5000",
        })
        self.assertEqual(parsed_config.environment, BingXEnvironment.PROD_VST)
        transport = FixtureSignedTransport()
        client = BingXSwapTestOrderClient(
            BingXSwapExecutionConfig(BingXEnvironment.PROD_VST, credentials),
            StaticSecretResolver({"sm://env/BINGX_API_KEY": "fixture-key", "sm://env/BINGX_SECRET_KEY": "fixture-secret"}),
            transport,
            clock_ms=lambda: 1_700_000_000_000,
        )
        order = BingXSwapOrder("BTC-USDT", "BUY", "LIMIT", Decimal("0.01"), "agent_1", Decimal("50000"))
        self.assertEqual(client.validate_order(order)["code"], 0)
        method, url, headers = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("/openApi/swap/v2/trade/order/test", url)
        self.assertEqual(headers["X-BX-APIKEY"], "fixture-key")
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        signature = query.pop("signature")
        expected = hmac.new(b"fixture-secret", canonical_parameter_string(query).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        with self.assertRaises(BingXLiveDispatchDisabled):
            client.submit_live_order(order)

    def test_environment_resolver_only_accepts_server_secret_injection(self) -> None:
        resolver = EnvironmentSecretResolver({"BINGX_API_KEY": "server-only-value"})
        self.assertEqual(resolver.resolve(SecretReference("sm://env/BINGX_API_KEY")), "server-only-value")
        with self.assertRaises(ValueError):
            resolver.resolve(SecretReference("vault://trading/key"))
        with self.assertRaises(LookupError):
            resolver.resolve(SecretReference("sm://env/MISSING_KEY"))

    def test_gemini_chart_advisory_is_server_side_and_cannot_replace_decision(self) -> None:
        market, report = snapshot()
        decision = CentralTradingIntelligenceEngine().decide((report, EngineReport(
            "second", "test", "H1", NOW, Direction.BUY, .55, MarketRegime.TRENDING_UP,
            (Evidence("second", "second", "H1", .5, .55, "volume", "fixture", "fixture", "volume.confirmation"),),
        )))
        policy = GeminiIntegrationPolicy(GeminiRole.ADVISORY_ONLY, SecretReference("sm://env/GEMINI_API_KEY"))
        transport = FixtureGeminiTransport()
        analyst = GeminiChartAnalyst(
            GeminiChartAnalysisConfig(policy, "gemini-test-model", maximum_candles=16),
            StaticSecretResolver({"sm://env/GEMINI_API_KEY": "gemini-server-only"}),
            transport,
        )
        advisory = analyst.analyze(market, "H1", decision, (report,))
        self.assertEqual(advisory.provider, "google_genai")
        self.assertEqual(advisory.model_id, "gemini-test-model")
        self.assertEqual(len(advisory.input_sha256), 64)
        self.assertIn("not a market-data source", advisory.disclaimer)
        self.assertNotIn("gemini-server-only", transport.calls[0]["contents"])
        self.assertIn("completed_ohlcv_only", transport.calls[0]["contents"])


if __name__ == "__main__":
    unittest.main()
