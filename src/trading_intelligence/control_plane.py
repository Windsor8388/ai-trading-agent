"""Minimal server-side control-plane API with no market-data or order-routing path.

This WSGI service is deliberately separate from ``dashboard/``.  It accepts no secret
from the browser and exposes only a redacted readiness snapshot at a same-origin API
path.  Its purpose is to make deployment configuration observable without claiming a
BingX connection or enabling execution.

The service intentionally has no endpoint that creates intents, sends orders, obtains
account balances, reads a secret value, or changes Gemini policy.  Those capabilities
must remain absent until a reviewed venue adapter and operational approval are added.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Callable, Mapping
from wsgiref.simple_server import make_server

from .integrations import (
    BingXRuntimeBinding,
    BingXVenueContract,
    GeminiIntegrationPolicy,
    IntegrationReadiness,
    LiveIntegrationGate,
    SecretReference,
)
from .signals import OperatingMode


API_PATH = "/api/v1/control-plane/status"
HEALTH_PATH = "/healthz"
API_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ControlPlaneConfiguration:
    """Safe runtime configuration comprising only secret-manager *references*.

    Deployment injects ``BINGX_API_KEY_REF`` and ``BINGX_API_SECRET_REF`` as opaque
    manager locators (for example ``vault://trading/bingx/api-key``).  The control plane
    never resolves them.  A raw credential fails validation before the process starts.
    """

    bingx_binding: BingXRuntimeBinding | None = None
    gemini_policy: GeminiIntegrationPolicy = GeminiIntegrationPolicy()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ControlPlaneConfiguration":
        source = os.environ if environ is None else environ
        key_ref = source.get("BINGX_API_KEY_REF")
        secret_ref = source.get("BINGX_API_SECRET_REF")
        if bool(key_ref) != bool(secret_ref):
            raise ValueError("BINGX_API_KEY_REF and BINGX_API_SECRET_REF must be set together or both omitted.")
        if not key_ref:
            return cls()
        contract_document = source.get("BINGX_VENUE_CONTRACT_JSON")
        if not contract_document:
            raise ValueError("BINGX_VENUE_CONTRACT_JSON is required with BingX secret references.")
        try:
            contract_payload = json.loads(contract_document)
        except json.JSONDecodeError as exc:
            raise ValueError("BINGX_VENUE_CONTRACT_JSON must be valid JSON.") from exc
        if not isinstance(contract_payload, dict):
            raise ValueError("BINGX_VENUE_CONTRACT_JSON must encode an object.")
        required_fields = {
            "documentation_url", "documentation_version", "api_base_url", "market_data_adapter_version",
            "execution_adapter_version", "account_scope", "client_order_id_supported",
            "order_status_reconciliation_supported", "cancel_supported", "websocket_recovery_supported",
        }
        if set(contract_payload) != required_fields:
            raise ValueError("BINGX_VENUE_CONTRACT_JSON must contain exactly the reviewed venue-contract fields.")
        verified = source.get("BINGX_READ_ONLY_VERIFIED", "false").strip().casefold() == "true"
        return cls(
            bingx_binding=BingXRuntimeBinding(
                api_key_ref=SecretReference(key_ref),
                api_secret_ref=SecretReference(secret_ref),
                venue_contract=BingXVenueContract(**contract_payload),
                market_data_read_only_verified=verified,
            ),
        )


@dataclass(frozen=True, slots=True)
class ControlPlaneStatus:
    """Browser-safe status: excludes references, account IDs, API values and venue URLs."""

    api_version: str
    observed_at: str
    operation_mode: str
    trade_dispatch: str
    bingx: IntegrationReadiness
    gemini: IntegrationReadiness

    def as_public_dict(self) -> dict[str, object]:
        def readiness(value: IntegrationReadiness) -> dict[str, object]:
            return {
                "state": value.state.value,
                "blockers": list(value.blockers),
                "safe_summary": value.safe_summary,
            }

        return {
            "api_version": self.api_version,
            "observed_at": self.observed_at,
            "operation_mode": self.operation_mode,
            "trade_dispatch": self.trade_dispatch,
            "bingx": readiness(self.bingx),
            "gemini": readiness(self.gemini),
        }


class RuntimeControlPlane:
    """Produces a redacted status snapshot; it is never an execution control surface."""

    version = "0.1.0"

    def __init__(self, configuration: ControlPlaneConfiguration | None = None) -> None:
        self.configuration = configuration or ControlPlaneConfiguration()

    def status(self, *, now: datetime | None = None) -> ControlPlaneStatus:
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("Control-plane status time must be timezone-aware.")
        return ControlPlaneStatus(
            api_version=API_VERSION,
            observed_at=observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            operation_mode=OperatingMode.ANALYSIS_ONLY.value,
            trade_dispatch="UNAVAILABLE",
            bingx=LiveIntegrationGate.bingx_readiness(self.configuration.bingx_binding, None, now=observed_at),
            gemini=LiveIntegrationGate.gemini_readiness(self.configuration.gemini_policy),
        )


def create_wsgi_application(control_plane: RuntimeControlPlane | None = None) -> Callable:
    """Create a GET-only WSGI app intended to be reverse-proxied at the dashboard origin."""
    plane = control_plane or RuntimeControlPlane()

    def application(environ: dict[str, object], start_response: Callable) -> list[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", ""))
        common_headers = [
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ]
        if method != "GET":
            start_response("405 Method Not Allowed", [("Allow", "GET"), *common_headers])
            return [b"Method not allowed.\n"]
        if path == HEALTH_PATH:
            start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"), *common_headers])
            return [b"ok\n"]
        if path != API_PATH:
            start_response("404 Not Found", [("Content-Type", "application/json; charset=utf-8"), *common_headers])
            return [b'{"error":"not_found"}\n']
        payload = json.dumps(plane.status().as_public_dict(), separators=(",", ":"), sort_keys=True).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"), *common_headers])
        return [payload]

    return application


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the status-only control plane for a reverse proxy/development environment."""
    configuration = ControlPlaneConfiguration.from_environment()
    with make_server(host, port, create_wsgi_application(RuntimeControlPlane(configuration))) as server:
        print(f"Trading Intelligence control-plane status API listening on http://{host}:{port}{API_PATH}")
        server.serve_forever()


if __name__ == "__main__":
    serve()
