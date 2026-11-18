import unittest

from trading_intelligence.knowledge import KnowledgeBase


class KnowledgeBaseTests(unittest.TestCase):
    def test_registry_loads_with_valid_sources_and_relations(self) -> None:
        base = KnowledgeBase.from_directory()
        fvg = base.get("ict.fair_value_gap")
        self.assertEqual(fvg.school, "ICT")
        self.assertIn("SRC-ICT-MENTORSHIP", fvg.source_ids)
        self.assertTrue(base.related("ict.fair_value_gap"))

    def test_retrieval_preserves_school_boundary(self) -> None:
        base = KnowledgeBase.from_directory()
        results = base.search("order", school="SMC")
        self.assertEqual([item.concept_id for item in results], ["smc.order_block"])


if __name__ == "__main__":
    unittest.main()

# The registry at repository root is canonical; the bundled copy enables installed-package use.
class BundledKnowledgeTests(unittest.TestCase):
    def test_bundled_registry_matches_canonical_registry(self) -> None:
        from pathlib import Path
        package_root = Path(__file__).resolve().parents[1] / "src" / "trading_intelligence" / "knowledge_data"
        repository_root = Path(__file__).resolve().parents[1] / "knowledge"
        self.assertEqual((package_root / "concepts.json").read_bytes(), (repository_root / "concepts.json").read_bytes())
        self.assertEqual((package_root / "sources.json").read_bytes(), (repository_root / "sources.json").read_bytes())
