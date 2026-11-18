from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


class DashboardContractTests(unittest.TestCase):
    def test_control_room_makes_offline_and_no_trade_boundaries_visible(self) -> None:
        page = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        for required in ("V0.5", "ANALYSIS_ONLY", "NO TRADE", "NO ENGINE INPUT", "لا يوجد مزود أسعار حي", "لا تنفيذ حي"):
            self.assertIn(required, page)
        self.assertIn('src="about:blank"', page)
        self.assertIn('sandbox="allow-scripts allow-forms allow-popups"', page)
        self.assertNotIn("s3.tradingview.com", page)

    def test_external_chart_is_opt_in_isolated_and_does_not_expose_engine_input(self) -> None:
        chart = (DASHBOARD / "chart.html").read_text(encoding="utf-8")
        app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
        self.assertIn('https://s3.tradingview.com/tv.js', chart)
        self.assertIn('new window.TradingView.widget', chart)
        self.assertIn("allowedSymbols", chart)
        self.assertIn("chartFrame.src = chartUrl.toString()", app)
        self.assertIn("chartFrame.src = 'about:blank'", app)
        self.assertIn("لا يُغذي محركات القرار", app)

    def test_vercel_headers_are_valid_and_limit_capabilities(self) -> None:
        config = json.loads((DASHBOARD / "vercel.json").read_text(encoding="utf-8"))
        headers = {header["key"]: header["value"] for header in config["headers"][0]["headers"]}
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("camera=()", headers["Permissions-Policy"])
        self.assertIn("https://s3.tradingview.com", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
