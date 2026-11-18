from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.execution import ExecutionMode, OrderLifecycleManager, OrderStatus
from trading_intelligence.models import DecisionAction, ProposedLevels, RiskAssessment, TradeSignal


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def candidate_signal() -> TradeSignal:
    return TradeSignal(
        instrument="XAUUSD", action=DecisionAction.BUY, timeframe="H1", strategy_ids=("unit",),
        levels=ProposedLevels(100, 99, (102,), "unit"), confidence=.7, data_source="fixture", created_at=NOW,
        risk=RiskAssessment(True, 1, 1, ("unit",), 2, 100, 100), supporting_evidence=(), opposing_reasons=(),
        invalidation_conditions=(), status="PAPER_CANDIDATE",
    )


class ExecutionLifecycleTests(unittest.TestCase):
    def test_analysis_mode_blocks_candidate_without_sending_order(self) -> None:
        record = OrderLifecycleManager().prepare(candidate_signal(), "order-analysis", NOW)
        self.assertEqual(record.status, OrderStatus.REJECTED)
        self.assertIn("analysis-only", record.rejection_reason or "")

    def test_paper_lifecycle_is_idempotent_and_tracks_fills(self) -> None:
        manager = OrderLifecycleManager(ExecutionMode.PAPER)
        prepared = manager.prepare(candidate_signal(), "order-1", NOW)
        duplicate = manager.prepare(candidate_signal(), "order-1", NOW)
        self.assertIs(prepared, duplicate)
        submitted = manager.record_submitted("order-1", "venue-1", NOW + timedelta(seconds=1))
        acknowledged = manager.record_acknowledged("order-1", NOW + timedelta(seconds=2))
        partial = manager.record_fill("order-1", .5, NOW + timedelta(seconds=3))
        filled = manager.record_fill("order-1", 1, NOW + timedelta(seconds=4))
        self.assertEqual(submitted.status, OrderStatus.SUBMITTED)
        self.assertEqual(acknowledged.status, OrderStatus.ACKNOWLEDGED)
        self.assertEqual(partial.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(len(filled.events), 5)

    def test_disconnect_requires_reconciliation_and_emergency_stop_blocks_new_intents(self) -> None:
        manager = OrderLifecycleManager(ExecutionMode.PAPER)
        manager.prepare(candidate_signal(), "order-2", NOW)
        manager.record_submitted("order-2", "venue-2", NOW + timedelta(seconds=1))
        unknown = next(order for order in manager.mark_disconnected(NOW + timedelta(seconds=2)) if order.client_order_id == "order-2")
        self.assertEqual(unknown.status, OrderStatus.UNKNOWN)
        reconciled = next(order for order in manager.reconcile({"order-2": OrderStatus.ACKNOWLEDGED}, NOW + timedelta(seconds=3)) if order.client_order_id == "order-2")
        self.assertEqual(reconciled.status, OrderStatus.ACKNOWLEDGED)
        stopped = next(order for order in manager.emergency_stop(NOW + timedelta(seconds=4), "unit drill") if order.client_order_id == "order-2")
        self.assertEqual(stopped.status, OrderStatus.CANCEL_REQUESTED)
        blocked = manager.prepare(candidate_signal(), "order-3", NOW + timedelta(seconds=5))
        self.assertEqual(blocked.status, OrderStatus.REJECTED)
        self.assertIn("Emergency stop", blocked.rejection_reason or "")


if __name__ == "__main__":
    unittest.main()
