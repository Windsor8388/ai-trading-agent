"""BingX USDT-M perpetual public OHLCV adapter, pinned to reviewed official docs.

The adapter implements only the documented public kline endpoint and deliberately does
not infer candle finality from a successful HTTP response.  The still-forming candle is
dropped before a :class:`MarketDataResponse` enters the quality pipeline.  It never
reads credentials and is usable for paper/research data only until a caller has
verified the live feed/entitlement it relies on.

Reviewed source (retrieved 2026-11-18):
``BingX-API/api-ai-skills`` commit ``5d96e72561c97de2ec3ba8650e48582c371cd4ad``:
``GET /openApi/swap/v3/quote/klines``.  The official reference says it returns
named ``open/high/low/close/volume/time`` fields and that the currently-forming candle
may be included.
"""
from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..data import timeframe_seconds, validate_closed_candles
from ..models import Candle, DataMode, FeedMetadata
from .contracts import BarRequest, MarketDataResponse


OFFICIAL_BINGX_API_SKILLS_URL = "https://github.com/BingX-API/api-ai-skills"
OFFICIAL_BINGX_API_SKILLS_REVISION = "5d96e72561c97de2ec3ba8650e48582c371cd4ad"
BINGX_SOURCE_HEADER = "BX-AI-SKILL"


class BingXEnvironment(str, Enum):
    PROD_LIVE = "prod-live"
    PROD_VST = "prod-vst"

    @property
    def base_urls(self) -> tuple[str, str]:
        if self is BingXEnvironment.PROD_LIVE:
            return ("https://open-api.bingx.com", "https://open-api.bingx.pro")
        return ("https://open-api-vst.bingx.com", "https://open-api-vst.bingx.pro")

    @property
    def data_mode(self) -> DataMode:
        return DataMode.LIVE if self is BingXEnvironment.PROD_LIVE else DataMode.PAPER


class BingXPublicJsonTransport(Protocol):
    def get_json(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        raise NotImplementedError


class UrllibBingXPublicTransport:
    """Optional HTTPS transport; it opens a connection only when a provider is called."""

    def get_json(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: fixed HTTPS destination registry
            charset = response.headers.get_content_charset() or "utf-8"
            decoded = json.loads(response.read().decode(charset))
        if not isinstance(decoded, Mapping):
            raise ValueError("BingX response must be a JSON object.")
        return decoded


@dataclass(frozen=True, slots=True)
class BingXSwapMarketConfig:
    """Explicit instrument map and environment; no symbol or session substitution."""

    environment: BingXEnvironment
    instrument_map: Mapping[str, str]
    instrument_kinds: Mapping[str, str]
    verified_public_market_connection: bool = False
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("BingX timeout_seconds must be positive.")
        if not self.instrument_map:
            raise ValueError("BingX configuration needs an explicit canonical-to-vendor instrument map.")
        for canonical, vendor in self.instrument_map.items():
            if not canonical or not vendor or canonical not in self.instrument_kinds:
                raise ValueError("Each BingX instrument needs a canonical id, vendor symbol, and instrument kind.")
            if "-" not in vendor or vendor != vendor.upper():
                raise ValueError("BingX swap symbols must be explicit uppercase BASE-QUOTE identifiers.")


class BingXSwapMarketDataProvider:
    """Maps only completed BingX public swap klines into the read-only provider contract."""

    name = "bingx_swap_public"
    version = "0.1.0"
    _endpoint = "/openApi/swap/v3/quote/klines"
    _timeframes = {
        "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h",
    }

    def __init__(self, config: BingXSwapMarketConfig, transport: BingXPublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    @staticmethod
    def _milliseconds(value: datetime) -> int:
        if value.tzinfo is None:
            raise ValueError("BingX request timestamps must be timezone-aware.")
        return int(value.astimezone(timezone.utc).timestamp() * 1000)

    @staticmethod
    def _number(record: Mapping[str, object], field: str) -> float:
        try:
            value = float(record[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"BingX kline lacks numeric {field!r}.") from exc
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(f"BingX kline {field!r} must be finite.")
        return value

    @staticmethod
    def _open_time(record: Mapping[str, object]) -> datetime:
        try:
            milliseconds = int(record["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("BingX kline lacks millisecond open time.") from exc
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)

    def _url(self, base_url: str, request: BarRequest, vendor_symbol: str) -> str:
        try:
            interval = self._timeframes[request.timeframe]
        except KeyError as exc:
            raise ValueError("BingX adapter supports M1/M5/M15/M30/H1/H4 only; daily/session policy is not configured.") from exc
        if request.price_component != "LAST":
            raise ValueError("BingX swap OHLCV has LAST-trade candles only; request price_component must be LAST.")
        parameters = {
            "symbol": vendor_symbol,
            "interval": interval,
            "startTime": self._milliseconds(request.start_utc),
            "endTime": self._milliseconds(request.end_utc),
            "limit": 1440,
        }
        return f"{base_url}{self._endpoint}?{urlencode(parameters)}"

    def _get(self, request: BarRequest, vendor_symbol: str) -> tuple[Mapping[str, Any], str]:
        headers = {"Accept": "application/json", "X-SOURCE-KEY": BINGX_SOURCE_HEADER, "User-Agent": "trading-intelligence-bingx-market/0.1"}
        primary, fallback = self.config.environment.base_urls
        primary_url = self._url(primary, request, vendor_symbol)
        try:
            return self.transport.get_json(primary_url, headers, self.config.timeout_seconds), primary_url
        except HTTPError:
            raise
        except (URLError, TimeoutError, socket.timeout, ConnectionError):
            fallback_url = self._url(fallback, request, vendor_symbol)
            return self.transport.get_json(fallback_url, headers, self.config.timeout_seconds), fallback_url

    def fetch_completed_bars(self, request: BarRequest) -> MarketDataResponse:
        try:
            vendor_symbol = self.config.instrument_map[request.instrument]
        except KeyError as exc:
            raise ValueError(f"BingX instrument is not configured: {request.instrument}") from exc
        payload, request_url = self._get(request, vendor_symbol)
        if payload.get("code") != 0:
            raise ValueError(f"BingX public kline request was rejected: code={payload.get('code')!r} msg={payload.get('msg')!r}")
        raw_bars = payload.get("data")
        if not isinstance(raw_bars, list):
            raise ValueError("BingX kline response has no data array.")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        as_of = min(request.end_utc, datetime.now(timezone.utc))
        seconds = timeframe_seconds(request.timeframe)
        candles: list[Candle] = []
        incomplete = 0
        out_of_range = 0
        for index, item in enumerate(raw_bars):
            if not isinstance(item, Mapping):
                raise ValueError(f"BingX kline {index} is not an object.")
            opened = self._open_time(item)
            closed = datetime.fromtimestamp(opened.timestamp() + seconds, tz=timezone.utc)
            if opened < request.start_utc or closed > request.end_utc:
                out_of_range += 1
                continue
            if closed > as_of:
                incomplete += 1
                continue
            candles.append(Candle(
                opened, closed, self._number(item, "open"), self._number(item, "high"),
                self._number(item, "low"), self._number(item, "close"), self._number(item, "volume"),
            ))
        quality = validate_closed_candles(candles, as_of)
        if not quality.passed:
            raise ValueError(f"BingX completed candles failed integrity validation: {'; '.join(quality.errors)}")
        warnings = list(quality.warnings)
        if incomplete:
            warnings.append(f"Dropped {incomplete} still-forming candle(s) from BingX response.")
        if out_of_range:
            warnings.append(f"Dropped {out_of_range} out-of-request-range candle(s) from BingX response.")
        feed = FeedMetadata(
            provider=self.name,
            instrument=request.instrument,
            instrument_kind=self.config.instrument_kinds[request.instrument],
            price_component="LAST",
            mode=self.config.environment.data_mode,
            retrieved_at=datetime.now(timezone.utc),
            vendor_symbol=vendor_symbol,
            source_url=request_url.split("?", 1)[0],
            live_feed_verified=self.config.verified_public_market_connection,
            quality_status="PASS" if not warnings else "PASS_WITH_WARNINGS",
            notes=(
                f"bingx_swap_public/{self.version}; docs={OFFICIAL_BINGX_API_SKILLS_REVISION}; "
                f"raw_sha256={raw_sha256}; incomplete_dropped={incomplete}; range_dropped={out_of_range}"
            ),
        )
        request_id = str(payload.get("requestId") or payload.get("timestamp") or raw_sha256[:16])
        return MarketDataResponse(tuple(candles), feed, request_id, raw_sha256, incomplete, tuple(warnings))
