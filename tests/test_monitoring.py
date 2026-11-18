from datetime import datetime, timedelta, timezone
import unittest

from trading_intelligence.monitoring import FeedKey, HealthStatus, OperationalIncident, OperationalMonitor


NOW = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)


class MonitoringTests(unittest.TestCase):
    def test_stale_feed_and_open_incident_degrade_health(self) -> None:
        monitor = OperationalMonitor()
        monitor.record_heartbeat(FeedKey("fixture", "XAUUSD", "H1"), NOW)
        healthy = monitor.snapshot(NOW + timedelta(seconds=10), stale_after=timedelta(seconds=30))
        self.assertEqual(healthy.overall, HealthStatus.HEALTHY)
        stale = monitor.snapshot(NOW + timedelta(seconds=31), stale_after=timedelta(seconds=30))
        self.assertEqual(stale.overall, HealthStatus.CRITICAL)
        monitor.record_heartbeat(FeedKey("fixture", "XAUUSD", "H1"), NOW + timedelta(seconds=31))
        monitor.open_incident(OperationalIncident("data-gap-1", "data_quality", HealthStatus.DEGRADED, NOW, "Gap awaiting review"))
        degraded = monitor.snapshot(NOW + timedelta(seconds=31), stale_after=timedelta(seconds=30))
        self.assertEqual(degraded.overall, HealthStatus.DEGRADED)
        monitor.resolve_incident("data-gap-1", NOW + timedelta(seconds=32))
        self.assertEqual(monitor.snapshot(NOW + timedelta(seconds=32), stale_after=timedelta(seconds=30)).overall, HealthStatus.HEALTHY)


if __name__ == "__main__":
    unittest.main()
