from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.backtest.engine import BacktestTrade, EquityPoint
from trading_intelligence.models import DecisionAction, InstrumentRiskSpec, PortfolioState, ProposedLevels, RiskLimits
from trading_intelligence.research import PerformanceAnalyzer
from trading_intelligence.risk import RiskManagementEngine
from trading_intelligence.risk_book import OpenRiskPosition, PortfolioRiskBook


T0 = datetime(2025, 1, 1, 9, tzinfo=timezone.utc)


def trade(strategy: str, timeframe: str, entry_time: datetime, net: float, return_on_equity: float) -> BacktestTrade:
    return BacktestTrade(
        strategy, timeframe, DecisionAction.BUY, entry_time - timedelta(hours=1), entry_time, entry_time + timedelta(hours=1),
        100, 101, 99, 102, 1, "UNIT", net, 0, net, return_on_equity, 2, 2, -1,
    )


class RiskBookAndAnalyticsTests(unittest.TestCase):
    def test_risk_book_tracks_utc_losses_drawdown_exposure_and_correlation(self) -> None:
        book = PortfolioRiskBook(10_000)
        book.open_position(OpenRiskPosition("p1", "XAUUSD", "metals", 1_000, T0))
        book.open_position(OpenRiskPosition("p2", "XAGUSD", "metals", 1_000, T0))
        live_state = book.portfolio_state(T0, candidate_correlation_group="metals")
        self.assertEqual(live_state.open_positions, 2)
        self.assertEqual(live_state.correlated_open_positions, 2)
        self.assertAlmostEqual(live_state.gross_exposure_fraction, .2)
        book.close_position("p1", -300, T0 + timedelta(hours=1))
        book.close_position("p2", 100, T0 + timedelta(hours=2))
        book.open_position(OpenRiskPosition("p3", "EURUSD", "fx", 500, T0 + timedelta(hours=2)))
        book.close_position("p3", -50, T0 + timedelta(hours=3))
        state = book.portfolio_state(T0 + timedelta(hours=4), event_risk_active=True, gap_risk_active=True)
        self.assertEqual(state.equity, 9_750)
        self.assertEqual(state.realized_daily_loss, 250)
        self.assertEqual(state.realized_weekly_loss, 250)
        self.assertAlmostEqual(state.drawdown_fraction, .025)
        self.assertEqual(state.consecutive_losses, 1)
        self.assertTrue(state.event_risk_active)
        self.assertTrue(state.gap_risk_active)

    def test_gap_buffer_sizes_to_worst_case_and_gap_embargo_blocks(self) -> None:
        engine = RiskManagementEngine(RiskLimits(gap_buffer_fraction=.5))
        levels = ProposedLevels(100, 99, (102,), "unit")
        spec = InstrumentRiskSpec("XAUUSD", 100, .01, .01)
        approved = engine.assess(DecisionAction.BUY, levels, PortfolioState(10_000), spec)
        self.assertTrue(approved.approved)
        self.assertEqual(approved.risk_per_unit, 150)
        self.assertLessEqual(approved.worst_case_cash_risk or 0, approved.max_cash_risk or 0)
        blocked = engine.assess(DecisionAction.BUY, levels, PortfolioState(10_000, gap_risk_active=True), spec)
        self.assertFalse(blocked.approved)
        self.assertTrue(any("gap-risk" in reason for reason in blocked.reasons))

    def test_performance_breakdown_has_sessions_timeframes_strategies_and_drawdowns(self) -> None:
        trades = (
            trade("strategy.a", "H1", T0, 20, .02),
            trade("strategy.b", "M15", T0.replace(hour=14), -10, -.01),
        )
        curve = (
            EquityPoint(T0, 100), EquityPoint(T0 + timedelta(hours=1), 120),
            EquityPoint(T0 + timedelta(hours=2), 90), EquityPoint(T0 + timedelta(hours=3), 130),
        )
        report = PerformanceAnalyzer(trade_periods_per_year=252).analyze(trades, curve)
        self.assertEqual(report.overall.trade_count, 2)
        self.assertEqual(report.overall.profit_factor, 2)
        self.assertEqual(set(report.by_strategy), {"strategy.a", "strategy.b"})
        self.assertEqual(set(report.by_timeframe), {"H1", "M15"})
        self.assertEqual(set(report.by_session), {"LONDON_UTC", "NEW_YORK_UTC"})
        self.assertEqual(len(report.drawdowns), 1)
        self.assertAlmostEqual(report.drawdowns[0].max_drawdown_fraction, -.25)
        self.assertEqual(report.drawdowns[0].recovered_time, T0 + timedelta(hours=3))


if __name__ == "__main__":
    unittest.main()
