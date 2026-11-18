from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from trading_intelligence.control_plane import (
    API_PATH,
    ControlPlaneConfiguration,
    RuntimeControlPlane,
    create_wsgi_application,
)


NOW = datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc)
CONTRACT = {
    "documentation_url": "https://official.example.test/bingx/api",
    "documentation_version": "reviewed-2026-11-18",
    "api_base_url": "https://api.example.test",
    "market_data_adapter_version": "1.0.0",
    "execution_adapter_version": "1.0.0",
    "account_scope": "SPOT",
    "client_order_id_supported": True,
    "order_status_reconciliation_supported": True,
    "cancel_supported": True,
    "websocket_recovery_supported": True,
}


def request(app, method: str, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({"REQUEST_METHOD": method, "PATH_INFO": path}, start_response))
    return captured["status"], captured["headers"], body  # type: ignore[return-value]


class ControlPlaneTests(unittest.TestCase):
    def test_default_status_is_redacted_analysis_only_and_does_not_claim_connection(self) -> None:
        status = RuntimeControlPlane().status(now=NOW).as_public_dict()
        self.assertEqual(status["operation_mode"], "ANALYSIS_ONLY")
        self.assertEqual(status["trade_dispatch"], "UNAVAILABLE")
        self.assertEqual(status["bingx"]["state"], "AWAITING_SECRET_BINDING")
        self.assertEqual(status["gemini"]["state"], "DISABLED")
        self.assertNotIn("vault://", json.dumps(status).casefold())
        self.assertNotIn("api-key", json.dumps(status).casefold())

    def test_environment_requires_secret_references_and_contract_and_never_serializes_them(self) -> None:
        environment = {
            "BINGX_API_KEY_REF": "vault://trading/bingx/api-key",
            "BINGX_API_SECRET_REF": "vault://trading/bingx/api-secret",
            "BINGX_VENUE_CONTRACT_JSON": json.dumps(CONTRACT),
            "BINGX_READ_ONLY_VERIFIED": "true",
        }
        configuration = ControlPlaneConfiguration.from_environment(environment)
        public = RuntimeControlPlane(configuration).status(now=NOW).as_public_dict()
        serialized = json.dumps(public)
        self.assertEqual(public["bingx"]["state"], "FORWARD_PAPER_READY")
        self.assertNotIn("vault://", serialized)
        self.assertNotIn("official.example.test", serialized)
        gemini_configuration = ControlPlaneConfiguration.from_environment({
            "GEMINI_ROLE": "ADVISORY_ONLY",
            "GEMINI_API_KEY_REF": "sm://env/GEMINI_API_KEY",
            "GEMINI_MODEL_ID": "gemini-test-model",
        })
        gemini_status = RuntimeControlPlane(gemini_configuration).status(now=NOW).as_public_dict()
        self.assertEqual(gemini_status["gemini"]["state"], "ADVISORY_READY")
        with self.assertRaises(ValueError):
            ControlPlaneConfiguration.from_environment({"BINGX_API_KEY_REF": "raw-key-value"})
        malformed_contract = {**CONTRACT, "cancel_supported": "true"}
        with self.assertRaises(ValueError):
            ControlPlaneConfiguration.from_environment({
                "BINGX_API_KEY_REF": "vault://trading/bingx/api-key",
                "BINGX_API_SECRET_REF": "vault://trading/bingx/api-secret",
                "BINGX_VENUE_CONTRACT_JSON": json.dumps(malformed_contract),
            })

    def test_wsgi_api_is_get_only_nostore_and_has_no_execution_route(self) -> None:
        app = create_wsgi_application(RuntimeControlPlane())
        status, headers, body = request(app, "GET", API_PATH)
        self.assertEqual(status, "200 OK")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(json.loads(body)["trade_dispatch"], "UNAVAILABLE")
        status, headers, _ = request(app, "POST", API_PATH)
        self.assertEqual(status, "405 Method Not Allowed")
        self.assertEqual(headers["Allow"], "GET")
        status, _, body = request(app, "GET", "/api/v1/orders")
        self.assertEqual(status, "404 Not Found")
        self.assertEqual(json.loads(body), {"error": "not_found"})


if __name__ == "__main__":
    unittest.main()
