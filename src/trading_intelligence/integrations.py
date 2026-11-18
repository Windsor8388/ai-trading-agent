"""Runtime integration contracts for external AI and venue services.

This module holds *references* to server-side secrets and a pinned, reviewed venue
contract.  It deliberately contains no provider keys, no browser-side configuration,
and no HTTP/order-routing implementation.  A future BingX adapter can only be supplied
after its API version, signing, endpoints, account permissions, and recovery semantics
have been reviewed against official documentation outside this repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class IntegrationState(str, Enum):
    DISABLED = "DISABLED"
    AWAITING_SECRET_BINDING = "AWAITING_SECRET_BINDING"
    AWAITING_VERIFIED_CONTRACT = "AWAITING_VERIFIED_CONTRACT"
    FORWARD_PAPER_READY = "FORWARD_PAPER_READY"
    ADVISORY_READY = "ADVISORY_READY"
    LIVE_BLOCKED = "LIVE_BLOCKED"
    LIVE_GATE_READY = "LIVE_GATE_READY"


class GeminiRole(str, Enum):
    DISABLED = "DISABLED"
    ADVISORY_ONLY = "ADVISORY_ONLY"
    EXPLANATIONS_ONLY = "EXPLANATIONS_ONLY"


@dataclass(frozen=True, slots=True)
class SecretReference:
    """A secret-manager locator, never a secret value or browser environment variable."""

    uri: str

    def __post_init__(self) -> None:
        if not self.uri or self.uri.strip() != self.uri:
            raise ValueError("Secret reference must be a non-empty trimmed URI.")
        if not self.uri.startswith(("secret://", "vault://", "sm://")):
            raise ValueError("Secret reference must use secret://, vault://, or sm://; never pass a raw credential.")
        if any(marker in self.uri.casefold() for marker in ("apikey=", "api_key=", "secret=", "token=")):
            raise ValueError("Secret references must not embed a raw credential value.")

    @property
    def scheme(self) -> str:
        return self.uri.split("://", 1)[0]


@dataclass(frozen=True, slots=True)
class GeminiIntegrationPolicy:
    """Gemini is bounded to text assistance; it cannot alter decision/risk/execution state."""

    role: GeminiRole = GeminiRole.DISABLED
    credential_ref: SecretReference | None = None

    def __post_init__(self) -> None:
        if self.role == GeminiRole.DISABLED and self.credential_ref is not None:
            raise ValueError("A disabled Gemini integration must not bind a secret reference.")
        if self.role != GeminiRole.DISABLED and self.credential_ref is None:
            raise ValueError("An enabled Gemini role requires a server-side secret reference.")


@dataclass(frozen=True, slots=True)
class BingXVenueContract:
    """Pinned deployment contract that must be filled from reviewed official venue docs.

    The repository intentionally has no default endpoints or signing algorithm because
    guessing a financial-venue API contract would be unsafe.  These fields document the
    exact review artefact a runtime adapter must satisfy before it receives a secret.
    """

    documentation_url: str
    documentation_version: str
    api_base_url: str
    market_data_adapter_version: str
    execution_adapter_version: str
    account_scope: str  # SPOT, FUTURES, or another explicitly reviewed account scope
    client_order_id_supported: bool
    order_status_reconciliation_supported: bool
    cancel_supported: bool
    websocket_recovery_supported: bool

    def __post_init__(self) -> None:
        required = (
            self.documentation_url, self.documentation_version, self.api_base_url,
            self.market_data_adapter_version, self.execution_adapter_version, self.account_scope,
        )
        if any(not value or value.strip() != value for value in required):
            raise ValueError("BingX venue contract requires non-empty pinned documentation/API fields.")
        if not self.documentation_url.startswith("https://") or not self.api_base_url.startswith("https://"):
            raise ValueError("BingX documentation and API base URLs must use HTTPS.")
        capabilities = (
            self.client_order_id_supported, self.order_status_reconciliation_supported,
            self.cancel_supported, self.websocket_recovery_supported,
        )
        if any(type(capability) is not bool for capability in capabilities):
            raise ValueError("BingX venue capabilities must be explicit booleans, not strings or inferred values.")


@dataclass(frozen=True, slots=True)
class BingXRuntimeBinding:
    """Secret references and reviewed contract required for a server-side adapter."""

    api_key_ref: SecretReference
    api_secret_ref: SecretReference
    venue_contract: BingXVenueContract
    market_data_read_only_verified: bool = False

    def __post_init__(self) -> None:
        if self.api_key_ref == self.api_secret_ref:
            raise ValueError("API key and secret must use distinct secret-manager references.")


@dataclass(frozen=True, slots=True)
class LiveOperationApproval:
    """Time-bounded human approval gate; a key alone is never approval to trade."""

    approval_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    permitted_instruments: tuple[str, ...]
    max_order_notional: float
    max_daily_loss: float
    forward_paper_drills_passed: bool
    position_reconciliation_verified: bool
    incident_and_emergency_stop_drill_passed: bool

    def __post_init__(self) -> None:
        if not self.approval_id or not self.approved_by:
            raise ValueError("Live approval needs an approval id and accountable human approver.")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None or self.expires_at <= self.approved_at:
            raise ValueError("Live approval timestamps must be aware and increasing.")
        if not self.permitted_instruments or any(not symbol.strip() for symbol in self.permitted_instruments):
            raise ValueError("Live approval needs at least one explicit permitted instrument.")
        if self.max_order_notional <= 0 or self.max_daily_loss <= 0:
            raise ValueError("Live approval limits must be positive.")


@dataclass(frozen=True, slots=True)
class IntegrationReadiness:
    integration: str
    state: IntegrationState
    blockers: tuple[str, ...]
    safe_summary: str


class LiveIntegrationGate:
    """Evaluate readiness without reading secrets or routing an order."""

    version = "0.1.0"

    @staticmethod
    def gemini_readiness(policy: GeminiIntegrationPolicy) -> IntegrationReadiness:
        if policy.role == GeminiRole.DISABLED:
            return IntegrationReadiness("gemini", IntegrationState.DISABLED, (), "Gemini is disabled by platform policy.")
        return IntegrationReadiness(
            "gemini", IntegrationState.AWAITING_SECRET_BINDING if policy.credential_ref is None else IntegrationState.ADVISORY_READY,
            ("Gemini output must remain advisory and cannot change risk/decision/execution state.",),
            f"Gemini role={policy.role.value}; server-side secret reference required.",
        )

    @staticmethod
    def bingx_readiness(
        binding: BingXRuntimeBinding | None,
        approval: LiveOperationApproval | None,
        *,
        now: datetime,
    ) -> IntegrationReadiness:
        if now.tzinfo is None:
            raise ValueError("Readiness timestamp must be timezone-aware.")
        if binding is None:
            return IntegrationReadiness(
                "bingx", IntegrationState.AWAITING_SECRET_BINDING,
                ("No server-side secret-manager binding is configured.", "No reviewed BingX API contract is configured."),
                "BingX is disconnected; browser/chart settings cannot connect an account.",
            )
        contract = binding.venue_contract
        contract_blockers = []
        if not binding.market_data_read_only_verified:
            contract_blockers.append("Read-only market-data entitlement/identity has not been verified.")
        if not contract.client_order_id_supported:
            contract_blockers.append("Venue contract does not confirm client-order-id idempotency support.")
        if not contract.order_status_reconciliation_supported:
            contract_blockers.append("Venue contract does not confirm order-status/position reconciliation support.")
        if not contract.cancel_supported:
            contract_blockers.append("Venue contract does not confirm cancel support.")
        if not contract.websocket_recovery_supported:
            contract_blockers.append("Venue contract does not confirm stream/reconnect recovery support.")
        if contract_blockers:
            return IntegrationReadiness("bingx", IntegrationState.AWAITING_VERIFIED_CONTRACT, tuple(contract_blockers), "Binding exists but cannot advance to forward paper/live gate.")
        if approval is None:
            return IntegrationReadiness("bingx", IntegrationState.FORWARD_PAPER_READY, ("No time-bounded live-operation approval exists.",), "Read-only/forward-paper integration may be evaluated; live dispatch remains blocked.")
        approval_blockers = []
        if approval.expires_at <= now:
            approval_blockers.append("Live-operation approval is expired.")
        if not approval.forward_paper_drills_passed:
            approval_blockers.append("Forward-paper drills have not passed.")
        if not approval.position_reconciliation_verified:
            approval_blockers.append("Position reconciliation is not verified.")
        if not approval.incident_and_emergency_stop_drill_passed:
            approval_blockers.append("Incident/emergency-stop drill has not passed.")
        if approval_blockers:
            return IntegrationReadiness("bingx", IntegrationState.LIVE_BLOCKED, tuple(approval_blockers), "Live dispatch remains blocked despite a binding/approval record.")
        return IntegrationReadiness(
            "bingx", IntegrationState.LIVE_BLOCKED,
            ("No BingX production-live order-routing adapter is included in this repository; a separately reviewed implementation is still required.",),
            "Human/quality gates are documented, but live dispatch is blocked because this code has no order sender.",
        )


def approved_instrument(approval: LiveOperationApproval, instrument: str, *, now: datetime) -> bool:
    """Check scope/expiry only; this does not approve a signal or send an order."""
    return approval.expires_at > now and instrument in approval.permitted_instruments
