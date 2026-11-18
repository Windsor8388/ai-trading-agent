"""Explicit, read-only OANDA v20 candle adapter.

No instance is created by application startup and this module never reads environment
variables. A caller must deliberately provide a read-only token and a transport. The
adapter is therefore executable, but this repository does not claim an active OANDA
connection or verified XAU/USD entitlement.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..data import timeframe_seconds, validate_closed_candles
from ..models import Candle, DataMode, FeedMetadata
from .contracts import BarRequest, MarketDataResponse


class JsonHttpTransport(Protocol):
    """Minimal injected transport. Tests can use a fixture without opening a socket."""

    def get_json(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        raise NotImplementedError


class UrllibJsonTransport:
    """Optional stdlib transport; network activity occurs only when its method is called."""

    def get_json(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: explicit caller-controlled HTTPS endpoint
            encoding = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(encoding))


@dataclass(frozen=True, slots=True)
class OandaV20Config:
    """Read-only API configuration supplied by an approved runtime, never by this repo."""

    access_token: str
    instrument_map: Mapping[str, str]
    instrument_kinds: Mapping[str, str]
    base_url: str = "https://api-fxpractice.oanda.com/v3"
    timeout_seconds: float = 15.0
    mode: DataMode = DataMode.PAPER
    verified_read_only_connection: bool = False
    price_component: str = "MID"

    def __post_init__(self) -> None:
        if not self.access_token.strip():
            raise ValueError("OANDA access token must be supplied by an approved runtime.")
        if not self.base_url.startswith("https://"):
            raise ValueError("OANDA base_url must use HTTPS.")
        if self.timeout_seconds <= 0:
            raise ValueError("OANDA timeout_seconds must be positive.")
        if self.price_component not in {"MID", "BID", "ASK"}:
            raise ValueError("OANDA price_component must be MID, BID, or ASK.")
        if not self.instrument_map:
            raise ValueError("OANDA configuration needs an explicit canonical-to-vendor instrument map.")
        for canonical, vendor in self.instrument_map.items():
            if not canonical or not vendor or canonical not in self.instrument_kinds:
                raise ValueError("Every OANDA mapped instrument needs canonical id, vendor symbol, and kind.")


class OandaV20MarketDataProvider:
    """Maps OANDA's completed candle response into the repository's read-only contract.

    It intentionally refuses D1/W1/MN because those sessions/alignment rules must be
    declared for the vendor/account before aggregating or comparing daily candles.
    """

    name = "oanda_v20"
    version = "0.1.0"
    _granularities = {"M1", "M5", "M15", "M30", "H1", "H4"}
    _price_key = {"MID": "mid", "BID": "bid", "ASK": "ask"}

    def __init__(self, config: OandaV20Config, transport: JsonHttpTransport) -> None:
        self.config = config
        self.transport = transport

    @staticmethod
    def _iso(timestamp: datetime) -> str:
        if timestamp.tzinfo is None:
            raise ValueError("Provider request timestamp must be timezone-aware.")
        return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("OANDA candle time must be an RFC3339 string.")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError as exc:
            raise ValueError(f"Invalid OANDA candle time: {value!r}") from exc

    @staticmethod
    def _number(container: Mapping[str, object], key: str) -> float:
        try:
            return float(container[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"OANDA candle is missing numeric {key!r}.") from exc

    def _url(self, request: BarRequest, vendor_instrument: str) -> str:
        if request.timeframe not in self._granularities:
            raise ValueError("OANDA adapter supports M1/M5/M15/M30/H1/H4 only; daily/session policy is not configured.")
        if request.price_component != self.config.price_component:
            raise ValueError("Request price component does not match the explicitly configured OANDA component.")
        query = urlencode({
            "price": self.config.price_component[0],
            "granularity": request.timeframe,
            "from": self._iso(request.start_utc),
            "to": self._iso(request.end_utc),
            "includeFirst": "true",
        })
        return f"{self.config.base_url}/instruments/{vendor_instrument}/candles?{query}"

    def fetch_completed_bars(self, request: BarRequest) -> MarketDataResponse:
        try:
            vendor_instrument = self.config.instrument_map[request.instrument]
        except KeyError as exc:
            raise ValueError(f"OANDA instrument is not configured: {request.instrument}") from exc
        url = self._url(request, vendor_instrument)
        payload = self.transport.get_json(
            url,
            {"Authorization": f"Bearer {self.config.access_token}", "Accept": "application/json", "User-Agent": "trading-intelligence-read-only/0.6"},
            self.config.timeout_seconds,
        )
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        raw_candles = payload.get("candles")
        if not isinstance(raw_candles, list):
            raise ValueError("OANDA response has no candle list.")
        price_key = self._price_key[self.config.price_component]
        seconds = timeframe_seconds(request.timeframe)
        candles: list[Candle] = []
        incomplete = 0
        for index, raw_candle in enumerate(raw_candles):
            if not isinstance(raw_candle, Mapping):
                raise ValueError(f"OANDA candle {index} is not an object.")
            if raw_candle.get("complete") is not True:
                incomplete += 1
                continue
            prices = raw_candle.get(price_key)
            if not isinstance(prices, Mapping):
                raise ValueError(f"OANDA completed candle {index} has no {price_key} prices.")
            opened = self._parse_time(raw_candle.get("time"))
            candles.append(Candle(
                opened,
                datetime.fromtimestamp(opened.timestamp() + seconds, tz=timezone.utc),
                self._number(prices, "o"), self._number(prices, "h"), self._number(prices, "l"), self._number(prices, "c"),
                float(raw_candle["volume"]) if raw_candle.get("volume") is not None else None,
            ))
        if not candles:
            raise ValueError("OANDA response contains no completed candles in the requested range.")
        quality = validate_closed_candles(candles, request.end_utc)
        if not quality.passed:
            raise ValueError(f"OANDA completed candles failed integrity validation: {'; '.join(quality.errors)}")
        warnings = list(quality.warnings)
        if incomplete:
            warnings.append(f"Dropped {incomplete} incomplete candle(s) from provider response.")
        feed = FeedMetadata(
            provider=self.name,
            instrument=request.instrument,
            instrument_kind=self.config.instrument_kinds[request.instrument],
            price_component=self.config.price_component,
            mode=self.config.mode,
            retrieved_at=datetime.now(timezone.utc),
            vendor_symbol=vendor_instrument,
            source_url=url.split("?")[0],
            live_feed_verified=self.config.verified_read_only_connection,
            quality_status="PASS" if not warnings else "PASS_WITH_WARNINGS",
            notes=f"oanda_v20/{self.version}; raw_sha256={raw_sha256}; incomplete_dropped={incomplete}",
        )
        request_id = str(payload.get("requestID") or raw_sha256[:16])
        return MarketDataResponse(tuple(candles), feed, request_id, raw_sha256, incomplete, tuple(warnings))
