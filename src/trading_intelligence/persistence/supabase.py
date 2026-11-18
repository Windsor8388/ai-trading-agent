"""Server-only, idempotent Supabase persistence for paper-order lifecycle facts.

The adapter uses a narrowly scoped RPC installed by the matching SQL migration.  It
never accepts credentials from a browser, never reads a market-data source, and never
submits an order.  A failed persistence request prevents the local lifecycle from
advancing; callers must retry the same immutable transition rather than infer success.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..execution.lifecycle import ExecutionEvent, OrderRecord
from ..integrations import SecretReference
from ..runtime.secrets import EnvironmentSecretResolver, SecretResolver


_RPC_PATH = "/rest/v1/rpc/ati_record_order_event"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class SupabasePersistenceError(RuntimeError):
    """A redacted persistence failure that intentionally excludes credential values."""


@dataclass(frozen=True, slots=True)
class SupabaseHttpResponse:
    status_code: int
    body: bytes


class SupabaseTransport(Protocol):
    """Minimal transport seam so unit tests never need a network or a credential."""

    def request(self, *, method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> SupabaseHttpResponse:
        """Send a server-side request and return the raw, bounded response."""


class UrllibSupabaseTransport:
    """stdlib transport; only instantiated by a trusted backend process."""

    def request(self, *, method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> SupabaseHttpResponse:
        request = Request(url, data=body, method=method, headers=dict(headers))
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URL is validated configuration.
                return SupabaseHttpResponse(response.status, response.read(256 * 1024))
        except HTTPError as exc:
            # The caller intentionally reports only a status class, not response text.
            return SupabaseHttpResponse(exc.code, exc.read(256 * 1024))
        except (URLError, TimeoutError, OSError) as exc:
            raise SupabasePersistenceError("Supabase lifecycle persistence transport failed; local state was not advanced.") from exc


@dataclass(frozen=True, slots=True)
class SupabasePersistenceConfig:
    """Validated public endpoint plus an opaque, server-side service-role locator."""

    url: str
    service_role_key_ref: SecretReference
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("SUPABASE_URL must be a bare HTTPS project URL without credentials, path, query, or fragment.")
        hostname = parsed.hostname.casefold()
        if hostname == "supabase.co" or not hostname.endswith(".supabase.co"):
            raise ValueError("SUPABASE_URL must use the canonical HTTPS <project-ref>.supabase.co endpoint.")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("Supabase persistence timeout_seconds must be between 0 and 30.")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "SupabasePersistenceConfig | None":
        """Read only a project URL and an opaque service-role *reference*.

        Both variables are intentionally required together. A raw service-role value
        fails `SecretReference` validation, making browser-style configuration or
        accidental credential injection a startup error.
        """
        source = environ if environ is not None else os.environ
        url = source.get("SUPABASE_URL")
        key_ref = source.get("SUPABASE_SERVICE_ROLE_KEY_REF")
        if not url and not key_ref:
            return None
        if not url or not key_ref:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY_REF must be set together or both omitted.")
        timeout_text = source.get("SUPABASE_PERSISTENCE_TIMEOUT_SECONDS", "5")
        try:
            timeout_seconds = float(timeout_text)
        except ValueError as exc:
            raise ValueError("SUPABASE_PERSISTENCE_TIMEOUT_SECONDS must be numeric.") from exc
        return cls(url=url, service_role_key_ref=SecretReference(key_ref), timeout_seconds=timeout_seconds)


class SupabaseLifecycleStore:
    """Atomically store an immutable intent and one contiguous lifecycle event.

    The migration's RPC locks each `client_order_id`, requires contiguous sequence
    numbers, compares retry payloads, and updates a query-friendly current state in the
    same transaction.  Thus an ambiguous HTTP result is never permission to generate a
    fresh id or skip an event: retry exactly the same lifecycle call.
    """

    version = "0.1.0"

    def __init__(
        self,
        configuration: SupabasePersistenceConfig,
        *,
        secret_resolver: SecretResolver | None = None,
        transport: SupabaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self.transport = transport or UrllibSupabaseTransport()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        secret_resolver: SecretResolver | None = None,
        transport: SupabaseTransport | None = None,
    ) -> "SupabaseLifecycleStore | None":
        configuration = SupabasePersistenceConfig.from_environment(environ)
        if configuration is None:
            return None
        return cls(configuration, secret_resolver=secret_resolver, transport=transport)

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("Supabase lifecycle persistence requires timezone-aware timestamps.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _intent_payload(cls, order: OrderRecord) -> dict[str, Any]:
        signal = order.signal
        levels = signal.levels
        return {
            "instrument": signal.instrument,
            "action": signal.action.value,
            "timeframe": signal.timeframe,
            "strategy_ids": list(signal.strategy_ids),
            "levels": None if levels is None else {
                "entry": levels.entry,
                "stop_loss": levels.stop_loss,
                "targets": list(levels.targets),
                "entry_condition": levels.entry_condition,
            },
            "risk": {
                "approved": signal.risk.approved,
                "quantity": signal.risk.quantity,
                "max_cash_risk": signal.risk.max_cash_risk,
                "worst_case_cash_risk": signal.risk.worst_case_cash_risk,
            },
            "signal_status": signal.status,
            "signal_created_at": cls._iso(signal.created_at),
            "execution_mode": order.mode.value,
        }

    @classmethod
    def _event_payload(cls, order: OrderRecord, event: ExecutionEvent) -> dict[str, Any]:
        return {
            "client_order_id": order.client_order_id,
            "request_fingerprint": order.request_fingerprint,
            "sequence": event.sequence,
            "occurred_at": cls._iso(event.at),
            "event_type": event.event_type,
            "detail": event.detail,
            "order_status": order.status.value,
            "venue_order_id": order.venue_order_id,
            "filled_quantity": order.filled_quantity,
            "rejection_reason": order.rejection_reason,
        }

    @staticmethod
    def _secret_value(value: str, reference: SecretReference) -> str:
        if not value or value.strip() != value:
            raise SupabasePersistenceError("Supabase service-role secret resolution returned an empty or malformed value.")
        if value == reference.uri:
            raise SupabasePersistenceError("Supabase service-role reference was not resolved in the trusted backend.")
        return value

    def record_transition(self, order: OrderRecord, event: ExecutionEvent) -> None:
        """Persist one event or raise without changing the in-memory lifecycle state."""
        if not _SHA256_HEX.fullmatch(order.request_fingerprint):
            raise ValueError("Order request fingerprint must be a lowercase SHA-256 hex digest.")
        if not order.events or order.events[-1] != event:
            raise ValueError("Only the most recent immutable lifecycle event may be persisted.")
        if event.sequence != len(order.events):
            raise ValueError("Lifecycle event sequence must match the immutable event history.")
        try:
            resolved_key = self.secret_resolver.resolve(self.configuration.service_role_key_ref)
        except (LookupError, ValueError):
            raise SupabasePersistenceError("Supabase service-role secret could not be resolved in the trusted backend.") from None
        key = self._secret_value(resolved_key, self.configuration.service_role_key_ref)
        event_payload = self._event_payload(order, event)
        payload = {
            "p_client_order_id": order.client_order_id,
            "p_request_fingerprint": order.request_fingerprint,
            "p_intent": self._intent_payload(order),
            "p_sequence": event.sequence,
            "p_occurred_at": event_payload["occurred_at"],
            "p_event_type": event.event_type,
            "p_detail": event.detail,
            "p_order_status": order.status.value,
            "p_venue_order_id": order.venue_order_id,
            "p_filled_quantity": order.filled_quantity,
            "p_rejection_reason": order.rejection_reason,
            "p_event_payload": event_payload,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self.transport.request(
            method="POST",
            url=f"{self.configuration.url.rstrip('/')}{_RPC_PATH}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
            body=body,
            timeout_seconds=self.configuration.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise SupabasePersistenceError(
                f"Supabase lifecycle persistence rejected the transition with HTTP {response.status_code}; local state was not advanced."
            )
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SupabasePersistenceError("Supabase lifecycle persistence returned an invalid response; local state was not advanced.") from exc
        # PostgREST returns a JSON object for a scalar RPC result. Accepting only the
        # two documented outcomes protects against accidentally calling a wrong RPC.
        if not isinstance(decoded, Mapping) or decoded.get("outcome") not in {"recorded", "duplicate"}:
            raise SupabasePersistenceError("Supabase lifecycle persistence returned an unexpected response; local state was not advanced.")
        if decoded.get("sequence") != event.sequence:
            raise SupabasePersistenceError("Supabase lifecycle persistence response sequence did not match; local state was not advanced.")
