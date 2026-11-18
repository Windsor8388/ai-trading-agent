from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from trading_intelligence.execution import ExecutionMode, ExecutionPolicy, OrderLifecycleManager, OrderStatus
from trading_intelligence.models import DecisionAction, InstrumentRiskSpec, ProposedLevels, RiskAssessment, TradeSignal
from trading_intelligence.persistence import (
    SupabaseHttpResponse,
    SupabaseLifecycleStore,
    SupabasePersistenceConfig,
    SupabasePersistenceError,
)
from trading_intelligence.research.ledger import ResearchLedger
from trading_intelligence.runtime.secrets import StaticSecretResolver


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
SPEC = InstrumentRiskSpec("XAUUSD", contract_multiplier=1, minimum_quantity=1, quantity_step=1)
POLICY = ExecutionPolicy({"XAUUSD": SPEC}, max_order_notional=200, max_order_cash_risk=100)
REFERENCE = "sm://env/SUPABASE_SERVICE_ROLE_KEY"


def candidate_signal() -> TradeSignal:
    return TradeSignal(
        instrument="XAUUSD", action=DecisionAction.BUY, timeframe="H1", strategy_ids=("supabase-test",),
        levels=ProposedLevels(100, 99, (102,), "unit"), confidence=.7, data_source="fixture", created_at=NOW,
        risk=RiskAssessment(True, 1, 1, ("unit",), 2, 100, 100), supporting_evidence=(), opposing_reasons=(),
        invalidation_conditions=(), status="PAPER_CANDIDATE",
    )


class CapturingTransport:
    def __init__(self, *, status_code: int = 200, outcome: str = "recorded") -> None:
        self.status_code = status_code
        self.outcome = outcome
        self.requests: list[dict[str, object]] = []

    def request(self, *, method: str, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float) -> SupabaseHttpResponse:
        payload = json.loads(body)
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "payload": payload, "timeout": timeout_seconds})
        response = {"outcome": self.outcome, "event_id": f"{payload['p_client_order_id']}:{payload['p_sequence']}", "sequence": payload["p_sequence"]}
        return SupabaseHttpResponse(self.status_code, json.dumps(response).encode("utf-8"))


class SupabasePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SupabasePersistenceConfig.from_environment({
            "SUPABASE_URL": "https://project-ref.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY_REF": REFERENCE,
            "SUPABASE_PERSISTENCE_TIMEOUT_SECONDS": "4.5",
        })
        assert self.config is not None

    def store(self, transport: CapturingTransport) -> SupabaseLifecycleStore:
        return SupabaseLifecycleStore(
            self.config,
            secret_resolver=StaticSecretResolver({REFERENCE: "service-role-fixture-value"}),
            transport=transport,
        )

    def test_configuration_is_disabled_only_when_both_values_are_absent(self) -> None:
        self.assertIsNone(SupabasePersistenceConfig.from_environment({}))
        with self.assertRaises(ValueError):
            SupabasePersistenceConfig.from_environment({"SUPABASE_URL": "https://project-ref.supabase.co"})
        with self.assertRaises(ValueError):
            SupabasePersistenceConfig.from_environment({"SUPABASE_SERVICE_ROLE_KEY_REF": REFERENCE})
        with self.assertRaises(ValueError):
            SupabasePersistenceConfig.from_environment({
                "SUPABASE_URL": "http://project-ref.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY_REF": REFERENCE,
            })
        with self.assertRaises(ValueError):
            SupabasePersistenceConfig.from_environment({
                "SUPABASE_URL": "https://untrusted.example",
                "SUPABASE_SERVICE_ROLE_KEY_REF": REFERENCE,
            })
        with self.assertRaises(ValueError):
            SupabasePersistenceConfig.from_environment({
                "SUPABASE_URL": "https://project-ref.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY_REF": "raw-service-role-value",
            })

    def test_atomic_rpc_payload_is_server_only_and_contains_contiguous_transitions(self) -> None:
        transport = CapturingTransport()
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY, durable_store=self.store(transport))
        prepared = manager.prepare(candidate_signal(), "supabase-order-1", NOW)
        submitted = manager.record_submitted("supabase-order-1", "venue-1", NOW + timedelta(seconds=1))

        self.assertEqual(prepared.status, OrderStatus.VALIDATED)
        self.assertEqual(submitted.status, OrderStatus.SUBMITTED)
        self.assertEqual(len(transport.requests), 2)
        first, second = transport.requests
        self.assertEqual(first["method"], "POST")
        self.assertEqual(first["url"], "https://project-ref.supabase.co/rest/v1/rpc/ati_record_order_event")
        self.assertEqual(first["payload"]["p_sequence"], 1)
        self.assertEqual(second["payload"]["p_sequence"], 2)
        self.assertEqual(first["payload"]["p_intent"]["instrument"], "XAUUSD")
        self.assertEqual(second["payload"]["p_venue_order_id"], "venue-1")
        self.assertNotIn(REFERENCE, json.dumps(first["payload"]))
        self.assertNotIn("service-role-fixture-value", json.dumps(first["payload"]))
        self.assertEqual(first["headers"]["Authorization"], "Bearer service-role-fixture-value")

    def test_rpc_rejection_leaves_local_lifecycle_unadvanced(self) -> None:
        transport = CapturingTransport(status_code=503)
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY, durable_store=self.store(transport))
        with self.assertRaises(SupabasePersistenceError) as raised:
            manager.prepare(candidate_signal(), "supabase-order-fails", NOW)
        self.assertIn("HTTP 503", str(raised.exception))
        self.assertEqual(manager.orders(), ())
        self.assertEqual(len(transport.requests), 1)

    def test_duplicate_rpc_outcome_is_a_safe_retry_of_the_same_immutable_transition(self) -> None:
        transport = CapturingTransport(outcome="duplicate")
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY, durable_store=self.store(transport))
        prepared = manager.prepare(candidate_signal(), "supabase-order-duplicate", NOW)
        retry = manager.prepare(candidate_signal(), "supabase-order-duplicate", NOW)
        self.assertIs(prepared, retry)
        self.assertEqual(len(transport.requests), 1)

    def test_lifecycle_rejects_ambiguous_two_audit_sinks(self) -> None:
        with self.assertRaises(ValueError):
            OrderLifecycleManager(
                ExecutionMode.PAPER,
                policy=POLICY,
                audit_ledger=ResearchLedger("unused.jsonl"),
                durable_store=self.store(CapturingTransport()),
            )


if __name__ == "__main__":
    unittest.main()
