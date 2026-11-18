from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading_intelligence.execution import (
    ExecutionMode,
    ExecutionPolicy,
    OrderLifecycleManager,
    OrderStatus,
    VenueOrderSnapshot,
)
from trading_intelligence.models import DecisionAction, InstrumentRiskSpec, ProposedLevels, RiskAssessment, TradeSignal
from trading_intelligence.research.ledger import ResearchLedger


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
SPEC = InstrumentRiskSpec("XAUUSD", contract_multiplier=1, minimum_quantity=1, quantity_step=1)
POLICY = ExecutionPolicy({"XAUUSD": SPEC}, max_order_notional=200, max_order_cash_risk=100)


def candidate_signal(*, quantity: float = 1, worst_case_cash_risk: float = 100) -> TradeSignal:
    return TradeSignal(
        instrument="XAUUSD", action=DecisionAction.BUY, timeframe="H1", strategy_ids=("unit",),
        levels=ProposedLevels(100, 99, (102,), "unit"), confidence=.7, data_source="fixture", created_at=NOW,
        risk=RiskAssessment(True, quantity, 1, ("unit",), 2, 100, worst_case_cash_risk), supporting_evidence=(), opposing_reasons=(),
        invalidation_conditions=(), status="PAPER_CANDIDATE",
    )


class ExecutionLifecycleTests(unittest.TestCase):
    def test_analysis_mode_blocks_candidate_without_sending_order(self) -> None:
        record = OrderLifecycleManager().prepare(candidate_signal(), "order-analysis", NOW)
        self.assertEqual(record.status, OrderStatus.REJECTED)
        self.assertIn("analysis-only", record.rejection_reason or "")

    def test_paper_requires_explicit_product_policy_and_applies_hard_limits(self) -> None:
        unconfigured = OrderLifecycleManager(ExecutionMode.PAPER).prepare(candidate_signal(), "missing-policy", NOW)
        oversized = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY).prepare(candidate_signal(quantity=3), "too-large", NOW)
        self.assertEqual(unconfigured.status, OrderStatus.REJECTED)
        self.assertIn("execution policy", unconfigured.rejection_reason or "")
        self.assertEqual(oversized.status, OrderStatus.REJECTED)
        self.assertIn("notional", oversized.rejection_reason or "")

    def test_paper_lifecycle_is_idempotent_tracks_fills_and_persists_hash_audit(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger = ResearchLedger(Path(temporary_directory) / "execution.jsonl")
            manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY, audit_ledger=ledger)
            prepared = manager.prepare(candidate_signal(), "order-1", NOW)
            duplicate = manager.prepare(candidate_signal(), "order-1", NOW)
            self.assertIs(prepared, duplicate)
            submitted = manager.record_submitted("order-1", "venue-1", NOW + timedelta(seconds=1))
            acknowledged = manager.record_acknowledged("order-1", NOW + timedelta(seconds=2))
            partial = manager.record_fill("order-1", .5, NOW + timedelta(seconds=3))
            filled = manager.record_fill("order-1", 1, NOW + timedelta(seconds=4))
            entries = ledger.verify()
        self.assertEqual(submitted.status, OrderStatus.SUBMITTED)
        self.assertEqual(acknowledged.status, OrderStatus.ACKNOWLEDGED)
        self.assertEqual(partial.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(len(filled.events), 5)
        self.assertEqual([entry.category for entry in entries], [
            "execution.validated", "execution.submitted", "execution.acknowledged", "execution.fill", "execution.fill",
        ])
        self.assertEqual(entries[-1].payload["order_status"], "FILLED")

    def test_disconnect_requires_snapshot_for_fill_and_emergency_stop_preserves_unknown(self) -> None:
        manager = OrderLifecycleManager(ExecutionMode.PAPER, policy=POLICY)
        manager.prepare(candidate_signal(), "order-2", NOW)
        manager.record_submitted("order-2", "venue-2", NOW + timedelta(seconds=1))
        unknown = next(order for order in manager.mark_disconnected(NOW + timedelta(seconds=2)) if order.client_order_id == "order-2")
        self.assertEqual(unknown.status, OrderStatus.UNKNOWN)
        self.assertFalse(manager.connected)
        with self.assertRaises(ValueError):
            manager.reconcile({"order-2": OrderStatus.FILLED}, NOW + timedelta(seconds=3))
        reconciled = next(order for order in manager.reconcile({
            "order-2": VenueOrderSnapshot(
                OrderStatus.PARTIALLY_FILLED, .5, NOW + timedelta(seconds=3), "venue-2", "snapshot-1",
            )
        }, NOW + timedelta(seconds=4)) if order.client_order_id == "order-2")
        self.assertEqual(reconciled.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(reconciled.filled_quantity, .5)
        manager.mark_disconnected(NOW + timedelta(seconds=5))
        stopped = next(order for order in manager.emergency_stop(NOW + timedelta(seconds=6), "unit drill") if order.client_order_id == "order-2")
        self.assertEqual(stopped.status, OrderStatus.UNKNOWN)
        self.assertEqual(stopped.events[-1].event_type, "EMERGENCY_RECONCILIATION_REQUIRED")
        blocked = manager.prepare(candidate_signal(), "order-3", NOW + timedelta(seconds=7))
        self.assertEqual(blocked.status, OrderStatus.REJECTED)
        self.assertIn("Emergency stop", blocked.rejection_reason or "")


if __name__ == "__main__":
    unittest.main()
