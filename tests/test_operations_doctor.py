from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading_intelligence.cli import main
from trading_intelligence.operations import DoctorState, PlatformDoctor
from trading_intelligence.research.ledger import ResearchLedger


NOW = datetime(2026, 11, 18, 18, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class OperationsDoctorTests(unittest.TestCase):
    def test_repository_doctor_passes_offline_checks_without_resolving_secrets(self) -> None:
        report = PlatformDoctor(ROOT).inspect(now=NOW)
        by_id = {check.check_id: check for check in report.checks}
        self.assertFalse(report.has_failures)
        self.assertEqual(by_id["knowledge_registry"].state, DoctorState.PASS)
        self.assertEqual(by_id["knowledge_bundle"].state, DoctorState.PASS)
        self.assertEqual(by_id["supabase_migration"].state, DoctorState.PASS)
        self.assertEqual(by_id["dashboard_boundary"].state, DoctorState.PASS)
        self.assertEqual(by_id["research_ledger"].state, DoctorState.SKIPPED)
        public = report.as_dict()
        self.assertTrue(public["healthy"])
        self.assertIn("no secrets were resolved", public["safety"])

    def test_doctor_rejects_committed_raw_secret_assignment(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            (root / "config" / "runtime.env.example").write_text(
                "BINGX_API_KEY=must-not-be-here\nBINGX_API_KEY_REF=sm://env/BINGX_API_KEY\nBINGX_API_SECRET_REF=sm://env/BINGX_SECRET_KEY\n",
                encoding="utf-8",
            )
            doctor = PlatformDoctor(root)
            check = doctor._check("runtime_template", "Committed secret-template boundary", doctor._runtime_template)
        self.assertEqual(check.state, DoctorState.FAIL)
        self.assertIn("BINGX_API_KEY", check.detail)

    def test_optional_ledger_is_verified_and_cli_doctor_emits_machine_readable_status(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            ledger_path = Path(temporary_directory) / "ledger.jsonl"
            ledger = ResearchLedger(ledger_path)
            ledger.append("operations.drill", {"result": "fixture"}, recorded_at=NOW)
            report = PlatformDoctor(ROOT).inspect(ledger_path=ledger_path, now=NOW)
        ledger_check = next(check for check in report.checks if check.check_id == "research_ledger")
        self.assertEqual(ledger_check.state, DoctorState.PASS)

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--doctor"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["healthy"])
        self.assertTrue(payload["generated_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
