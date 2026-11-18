import unittest

from trading_intelligence.research.metrics import monte_carlo_bootstrap, performance_metrics, walk_forward_splits


class ResearchMetricTests(unittest.TestCase):
    def test_metrics_do_not_invent_profit_factor_without_losses(self) -> None:
        report = performance_metrics((.01, .02), 252)
        self.assertIsNone(report.profit_factor)
        self.assertGreater(report.total_return, 0)

    def test_walk_forward_is_strictly_temporal(self) -> None:
        folds = walk_forward_splits(30, 10, 5)
        self.assertEqual(len(folds), 4)
        self.assertTrue(all(train.stop <= test.start for train, test in folds))

    def test_bootstrap_is_reproducible(self) -> None:
        self.assertEqual(monte_carlo_bootstrap((.01, -.02, .03), 5, seed=9), monte_carlo_bootstrap((.01, -.02, .03), 5, seed=9))


if __name__ == "__main__":
    unittest.main()
