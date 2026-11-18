from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.models import Candle, DecisionAction, InstrumentRiskSpec, ProposedLevels, RiskAssessment, TradeSignal
from trading_intelligence.paper import PaperExecutionSimulator, PaperOrderState


NOW = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)


def paper_signal() -> TradeSignal:
    return TradeSignal(
        instrument="XAUUSD", action=DecisionAction.BUY, timeframe="H1", strategy_ids=("unit",),
        levels=ProposedLevels(100, 98, (102,), "unit"), confidence=.7, data_source="fixture", created_at=NOW,
        risk=RiskAssessment(True, 1, 2, ("unit",), 1), supporting_evidence=(), opposing_reasons=(),
        invalidation_conditions=(), status="PAPER_CANDIDATE",
    )


class PaperSimulatorTests(unittest.TestCase):
    def test_idempotent_submit_and_conservative_exit(self) -> None:
        simulator = PaperExecutionSimulator(InstrumentRiskSpec("XAUUSD", 1, 1, 1))
        first = simulator.submit(paper_signal(), "signal-001", NOW)
        again = simulator.submit(paper_signal(), "signal-001", NOW)
        self.assertIs(first, again)
        candle = Candle(NOW, NOW + timedelta(hours=1), 100, 103, 97, 100, 10)
        final = simulator.process_completed_candle(candle)[0]
        self.assertEqual(final.state, PaperOrderState.CLOSED)
        self.assertEqual(final.exit_reason, "STOP_FIRST_AMBIGUOUS")
        self.assertEqual(final.exit_price, 98)

    def test_halt_cancels_pending_and_blocks_new_orders(self) -> None:
        simulator = PaperExecutionSimulator(InstrumentRiskSpec("XAUUSD", 1, 1, 1))
        simulator.submit(paper_signal(), "pending", NOW)
        halted = simulator.halt()
        self.assertEqual(halted[0].state, PaperOrderState.CANCELLED)
        rejected = simulator.submit(paper_signal(), "after-halt", NOW)
        self.assertEqual(rejected.state, PaperOrderState.REJECTED)


if __name__ == "__main__":
    unittest.main()
