"""BingX USDT-M signed test-order boundary.

This module uses the official HMAC-SHA256 authentication rules pinned in
``providers.bingx_swap``.  It supports server-side, simulated-environment order
*validation* against BingX's documented ``/order/test`` endpoint.  It deliberately
has no live-place-order method: a test-order success is not evidence that a real order
would be accepted, and this repository's signal engine still blocks live dispatch.

No retry is performed for signed writes.  A lost response is an unknown venue outcome
that must be reconciled, not a reason to submit the same order again.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from decimal import Decimal
from hashlib import sha256
import hmac
import json
import re
import socket
from time import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..integrations import SecretReference
from ..providers.bingx_swap import BINGX_SOURCE_HEADER, BingXEnvironment
from ..runtime.secrets import SecretResolver


class BingXSignedTransport(Protocol):
    def request_json(self, method: str, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        raise NotImplementedError


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


class UrllibBingXSignedTransport:
    """Stdlib transport with redirects disabled as required by BingX's destination policy."""

    def request_json(self, method: str, url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method=method)
        opener = build_opener(_RejectRedirects())
        with opener.open(request, timeout=timeout_seconds) as response:  # nosec B310: fixed official HTTPS registry
            charset = response.headers.get_content_charset() or "utf-8"
            decoded = json.loads(response.read().decode(charset))
        if not isinstance(decoded, Mapping):
            raise ValueError("BingX signed response must be a JSON object.")
        return decoded


class BingXVenueOutcomeUnknown(RuntimeError):
    """The write may have reached the venue; reconcile with order query before retrying."""


class BingXLiveDispatchDisabled(RuntimeError):
    """Raised instead of sending a live order from this guarded integration release."""


@dataclass(frozen=True, slots=True)
class BingXCredentials:
    """Server-side references only; secret values are resolved at request time and not retained."""

    api_key_ref: SecretReference
    secret_key_ref: SecretReference

    def __post_init__(self) -> None:
        if self.api_key_ref == self.secret_key_ref:
            raise ValueError("BingX API key and secret key must have distinct references.")


@dataclass(frozen=True, slots=True)
class BingXSwapExecutionConfig:
    environment: BingXEnvironment
    credentials: BingXCredentials
    timeout_seconds: float = 15.0
    recv_window_ms: int = 5000

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("BingX signed request timeout must be positive.")
        if not 1 <= self.recv_window_ms <= 5000:
            raise ValueError("BingX recvWindow must be between 1 and 5000 milliseconds.")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "BingXSwapExecutionConfig":
        """Read non-secret configuration and references; secret values stay with the resolver."""
        source = os.environ if environ is None else environ
        try:
            environment = BingXEnvironment(source.get("BINGX_ENVIRONMENT", BingXEnvironment.PROD_VST.value))
        except ValueError as exc:
            raise ValueError("BINGX_ENVIRONMENT must be prod-vst or prod-live.") from exc
        key_ref = source.get("BINGX_API_KEY_REF")
        secret_ref = source.get("BINGX_API_SECRET_REF")
        if not key_ref or not secret_ref:
            raise ValueError("BINGX_API_KEY_REF and BINGX_API_SECRET_REF are required for signed BingX calls.")
        try:
            timeout = float(source.get("BINGX_TIMEOUT_SECONDS", "15"))
            recv_window = int(source.get("BINGX_RECV_WINDOW_MS", "5000"))
        except ValueError as exc:
            raise ValueError("BINGX_TIMEOUT_SECONDS/BINGX_RECV_WINDOW_MS must be numeric.") from exc
        return cls(
            environment=environment,
            credentials=BingXCredentials(SecretReference(key_ref), SecretReference(secret_ref)),
            timeout_seconds=timeout,
            recv_window_ms=recv_window,
        )


@dataclass(frozen=True, slots=True)
class BingXSwapOrder:
    """Narrow, validated order shape accepted by the VST test endpoint only."""

    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    client_order_id: str
    price: Decimal | None = None
    position_side: str = "BOTH"
    reduce_only: bool = False

    _client_id = re.compile(r"^[A-Za-z0-9_]{1,40}$")

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper() or "-" not in self.symbol:
            raise ValueError("BingX swap symbol must be an uppercase BASE-QUOTE value.")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("BingX swap side must be BUY or SELL.")
        if self.order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("This guarded adapter permits MARKET or LIMIT only; conditional types need a separate reviewed policy.")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("BingX order quantity must be finite and positive.")
        if not self._client_id.fullmatch(self.client_order_id):
            raise ValueError("BingX client_order_id must contain 1-40 letters, digits, or underscores.")
        if self.position_side not in {"BOTH", "LONG", "SHORT"}:
            raise ValueError("BingX position_side must be BOTH, LONG, or SHORT.")
        if self.reduce_only and self.position_side != "BOTH":
            raise ValueError("BingX reduce_only is allowed only with one-way position_side=BOTH.")
        if self.order_type == "LIMIT" and (self.price is None or not self.price.is_finite() or self.price <= 0):
            raise ValueError("BingX LIMIT test orders require a finite positive price.")
        if self.order_type == "MARKET" and self.price is not None:
            raise ValueError("BingX MARKET test orders must not include a price.")

    def parameters(self) -> dict[str, str]:
        result = {
            "symbol": self.symbol,
            "side": self.side,
            "type": self.order_type,
            "quantity": _decimal_text(self.quantity),
            "clientOrderId": self.client_order_id,
            "positionSide": self.position_side,
            "reduceOnly": _bool_text(self.reduce_only),
        }
        if self.price is not None:
            result["price"] = _decimal_text(self.price)
            result["timeInForce"] = "GTC"
        return result


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Decimal parameter must be finite.")
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _parameter_text(value: object) -> str:
    if isinstance(value, bool):
        return _bool_text(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, Mapping)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise TypeError(f"Unsupported BingX parameter type: {type(value).__name__}")


def canonical_parameter_string(parameters: Mapping[str, object]) -> str:
    """ASCII-sort and serialize parameters exactly once before HMAC signing."""
    if "signature" in parameters:
        raise ValueError("signature must not be supplied to canonical_parameter_string.")
    normalized: dict[str, str] = {}
    for key, value in parameters.items():
        if not key or not key.isascii() or not all(character.isalnum() or character == "_" for character in key):
            raise ValueError("BingX parameter names must be non-empty ASCII identifier keys.")
        normalized[key] = _parameter_text(value)
    return "&".join(f"{key}={normalized[key]}" for key in sorted(normalized))


def sign_bingx_parameters(secret_key: str, parameters: Mapping[str, object]) -> str:
    if not secret_key:
        raise ValueError("BingX secret key cannot be empty.")
    canonical = canonical_parameter_string(parameters)
    return hmac.new(secret_key.encode("utf-8"), canonical.encode("utf-8"), sha256).hexdigest()


def _query(parameters: Mapping[str, object]) -> str:
    # Values are encoded only for the URL. The signing string always uses unencoded values.
    return "&".join(
        f"{key}={quote(_parameter_text(value), safe='-_.~')}"
        for key, value in parameters.items()
    )


class BingXSwapTestOrderClient:
    """Server-side VST test-order/query client; no real order dispatch path is exposed."""

    version = "0.1.0"
    _test_order_path = "/openApi/swap/v2/trade/order/test"
    _query_order_path = "/openApi/swap/v2/trade/order"

    def __init__(
        self,
        config: BingXSwapExecutionConfig,
        secret_resolver: SecretResolver,
        transport: BingXSignedTransport,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self.secret_resolver = secret_resolver
        self.transport = transport
        self._clock_ms = clock_ms or (lambda: int(time() * 1000))

    def _signed_url_and_headers(self, path: str, parameters: Mapping[str, object]) -> tuple[str, Mapping[str, str]]:
        if not path.startswith("/openApi/swap/"):
            raise ValueError("Only the reviewed BingX swap destination registry is permitted.")
        values = dict(parameters)
        if "timestamp" in values or "recvWindow" in values or "signature" in values:
            raise ValueError("Caller must not supply BingX timestamp, recvWindow, or signature.")
        values["timestamp"] = self._clock_ms()
        values["recvWindow"] = self.config.recv_window_ms
        api_key = self.secret_resolver.resolve(self.config.credentials.api_key_ref)
        secret_key = self.secret_resolver.resolve(self.config.credentials.secret_key_ref)
        values["signature"] = sign_bingx_parameters(secret_key, values)
        primary, _fallback = self.config.environment.base_urls
        return (
            f"{primary}{path}?{_query(values)}",
            {
                "Accept": "application/json",
                "X-BX-APIKEY": api_key,
                "X-SOURCE-KEY": BINGX_SOURCE_HEADER,
                "User-Agent": "trading-intelligence-bingx-test-order/0.1",
            },
        )

    @staticmethod
    def _validated_response(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload.get("code") != 0:
            raise ValueError(f"BingX private request was rejected: code={payload.get('code')!r} msg={payload.get('msg')!r}")
        return payload

    def validate_order(self, order: BingXSwapOrder) -> Mapping[str, Any]:
        """Call BingX's no-fill test endpoint in VST only; never retries a write."""
        if self.config.environment is not BingXEnvironment.PROD_VST:
            raise BingXLiveDispatchDisabled("Order validation is restricted to BingX prod-vst; production-live dispatch is not enabled.")
        url, headers = self._signed_url_and_headers(self._test_order_path, order.parameters())
        try:
            return self._validated_response(self.transport.request_json("POST", url, headers, self.config.timeout_seconds))
        except (HTTPError, URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            raise BingXVenueOutcomeUnknown(
                "BingX test-order outcome is unknown after a transport failure; inspect the venue before retrying."
            ) from exc

    def query_order(self, symbol: str, *, client_order_id: str | None = None, order_id: str | None = None) -> Mapping[str, Any]:
        """Reconcile a known venue order by exactly one identifier; no fallback destination is used."""
        if not symbol or symbol != symbol.upper() or "-" not in symbol:
            raise ValueError("BingX reconciliation symbol must be uppercase BASE-QUOTE.")
        if bool(client_order_id) == bool(order_id):
            raise ValueError("Provide exactly one of client_order_id or order_id for BingX reconciliation.")
        parameters: dict[str, str] = {"symbol": symbol}
        if client_order_id:
            if not BingXSwapOrder._client_id.fullmatch(client_order_id):
                raise ValueError("Invalid BingX client_order_id.")
            parameters["clientOrderId"] = client_order_id
        if order_id:
            parameters["orderId"] = order_id
        url, headers = self._signed_url_and_headers(self._query_order_path, parameters)
        return self._validated_response(self.transport.request_json("GET", url, headers, self.config.timeout_seconds))

    def submit_live_order(self, order: BingXSwapOrder) -> None:
        """Explicitly unavailable: human approval/live adapter work is a separate release gate."""
        raise BingXLiveDispatchDisabled(
            "Live BingX order dispatch is disabled. Use VST test validation, reconciliation drills, and a separately approved live adapter."
        )
