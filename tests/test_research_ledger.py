from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from trading_intelligence.research import ResearchLedger


class ResearchLedgerTests(unittest.TestCase):
    def test_append_only_hash_chain_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(Path(directory) / "research.jsonl")
            first = ledger.append("prediction", {"instrument": "XAUUSD", "direction": "NO_TRADE"}, recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
            second = ledger.append("outcome", {"prediction_sequence": 1, "resolved": True}, recorded_at=datetime(2025, 1, 2, tzinfo=timezone.utc))
            verified = ledger.verify()
        self.assertEqual([item.sequence for item in verified], [1, 2])
        self.assertEqual(second.parent_hash, first.entry_hash)

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.jsonl"
            ledger = ResearchLedger(path)
            ledger.append("experiment", {"name": "frozen"}, recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
            path.write_text(path.read_text(encoding="utf-8").replace("frozen", "altered"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                ledger.verify()


if __name__ == "__main__":
    unittest.main()
