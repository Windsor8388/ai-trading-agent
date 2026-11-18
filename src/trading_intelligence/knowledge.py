"""Versioned local knowledge-base reader with source-reference integrity checks.

The registry is deliberately deterministic and reviewable.  It stores operational
hypotheses and provenance, not assertions that a trading concept has predictive value.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    source_id: str
    title: str
    publisher: str
    url: str
    published_or_accessed: str
    source_version: str
    source_type: str
    use: str


@dataclass(frozen=True, slots=True)
class KnowledgeConcept:
    concept_id: str
    school: str
    name: str
    rule_version: str
    updated_at: str
    operational_rule: str
    inputs: tuple[str, ...]
    use_cases: tuple[str, ...]
    signal_validity: str
    invalidation: str
    examples: tuple[str, ...]
    common_failures: tuple[str, ...]
    evidence_notes: str
    tags: tuple[str, ...]
    relations: tuple[str, ...]
    evidence_status: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeRetrieval:
    """A deterministic retrieval result with explainable relevance, not vector similarity."""

    concept: KnowledgeConcept
    score: int
    matched_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeStrategy:
    strategy_id: str
    name: str
    strategy_version: str
    updated_at: str
    schools: tuple[str, ...]
    concept_ids: tuple[str, ...]
    inputs: tuple[str, ...]
    setup_rules: tuple[str, ...]
    confirmation_rules: tuple[str, ...]
    invalidation_rules: tuple[str, ...]
    no_trade_conditions: tuple[str, ...]
    example: str
    common_failure: str
    evidence_status: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyRetrieval:
    strategy: KnowledgeStrategy
    score: int
    matched_fields: tuple[str, ...]


class KnowledgeBase:
    """Read-only concept retrieval for analysis/reporting and human review.

    Runtime rules remain versioned code; this registry supplies their provenance,
    relationships, documented assumptions, examples, failure modes, and missing-input
    requirements. Loading fails fast if any concept points to a missing source/relation
    or omits the structured review fields required by this schema.
    """

    def __init__(
        self,
        concepts: Iterable[KnowledgeConcept],
        sources: Iterable[KnowledgeSource],
        version: str,
        updated_at: str,
        strategies: Iterable[KnowledgeStrategy] = (),
    ) -> None:
        concept_values = tuple(concepts)
        source_values = tuple(sources)
        strategy_values = tuple(strategies)
        self._concepts = {concept.concept_id: concept for concept in concept_values}
        self._sources = {source.source_id: source for source in source_values}
        self._strategies = {strategy.strategy_id: strategy for strategy in strategy_values}
        self.version = version
        self.updated_at = updated_at
        if len(self._concepts) != len(concept_values) or len(self._sources) != len(source_values) or len(self._strategies) != len(strategy_values):
            raise ValueError("Knowledge concepts, sources, and strategies must have unique ids.")
        self._validate()

    @classmethod
    def from_directory(cls, directory: Path | str | None = None) -> "KnowledgeBase":
        if directory is not None:
            root = Path(directory)
        else:
            repository_registry = Path(__file__).resolve().parents[2] / "knowledge"
            bundled_registry = Path(__file__).resolve().parent / "knowledge_data"
            root = repository_registry if repository_registry.is_dir() else bundled_registry
        with (root / "concepts.json").open(encoding="utf-8") as file:
            concept_document: dict[str, Any] = json.load(file)
        with (root / "sources.json").open(encoding="utf-8") as file:
            source_document: dict[str, Any] = json.load(file)
        with (root / "strategies.json").open(encoding="utf-8") as file:
            strategy_document: dict[str, Any] = json.load(file)
        try:
            concepts = [
                KnowledgeConcept(
                    concept_id=item["id"], school=item["school"], name=item["name"],
                    rule_version=item["rule_version"], updated_at=item["updated_at"],
                    operational_rule=item["operational_rule"], inputs=tuple(item["inputs"]),
                    use_cases=tuple(item["use_cases"]), signal_validity=item["signal_validity"],
                    invalidation=item["invalidation"], examples=tuple(item["examples"]),
                    common_failures=tuple(item["common_failures"]), evidence_notes=item["evidence_notes"],
                    tags=tuple(item["tags"]), relations=tuple(item["relations"]),
                    evidence_status=item["evidence_status"], source_ids=tuple(item["source_ids"]),
                )
                for item in concept_document["concepts"]
            ]
            sources = [
                KnowledgeSource(
                    source_id=item["id"], title=item["title"], publisher=item["publisher"], url=item["url"],
                    published_or_accessed=item["published_or_accessed"], source_version=item["source_version"],
                    source_type=item["type"], use=item["use"],
                )
                for item in source_document["sources"]
            ]
            strategies = [
                KnowledgeStrategy(
                    strategy_id=item["id"], name=item["name"], strategy_version=item["strategy_version"],
                    updated_at=item["updated_at"], schools=tuple(item["schools"]), concept_ids=tuple(item["concept_ids"]),
                    inputs=tuple(item["inputs"]), setup_rules=tuple(item["setup_rules"]),
                    confirmation_rules=tuple(item["confirmation_rules"]), invalidation_rules=tuple(item["invalidation_rules"]),
                    no_trade_conditions=tuple(item["no_trade_conditions"]), example=item["example"],
                    common_failure=item["common_failure"], evidence_status=item["evidence_status"], source_ids=tuple(item["source_ids"]),
                )
                for item in strategy_document["strategies"]
            ]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Knowledge registry does not satisfy schema {concept_document.get('schema_version', 'unknown')!r}.") from exc
        if strategy_document.get("updated_at") != concept_document["updated_at"]:
            raise ValueError("Strategy and concept registries must declare the same current update date.")
        return cls(concepts, sources, concept_document["schema_version"], concept_document["updated_at"], strategies)

    @staticmethod
    def _all_text(values: Iterable[str]) -> bool:
        return all(isinstance(value, str) and bool(value.strip()) for value in values)

    def _validate(self) -> None:
        if not self._concepts:
            raise ValueError("Knowledge base has no concepts.")
        if not self._sources:
            raise ValueError("Knowledge base has no sources.")
        for source in self._sources.values():
            if not self._all_text((source.source_id, source.title, source.publisher, source.url, source.published_or_accessed, source.source_version, source.source_type, source.use)):
                raise ValueError(f"Knowledge source {source.source_id!r} has empty required provenance fields.")
            if not source.url.startswith("https://"):
                raise ValueError(f"Knowledge source {source.source_id!r} must use an HTTPS URL.")
        for concept in self._concepts.values():
            unknown_sources = set(concept.source_ids) - set(self._sources)
            unknown_relations = set(concept.relations) - set(self._concepts)
            required_scalars = (
                concept.concept_id, concept.school, concept.name, concept.rule_version, concept.updated_at,
                concept.operational_rule, concept.signal_validity, concept.invalidation, concept.evidence_notes,
                concept.evidence_status,
            )
            required_lists = (concept.inputs, concept.use_cases, concept.examples, concept.common_failures, concept.tags, concept.source_ids)
            if not self._all_text(required_scalars) or any(not values or not self._all_text(values) for values in required_lists):
                raise ValueError(f"{concept.concept_id} has empty structured knowledge fields.")
            if unknown_sources:
                raise ValueError(f"{concept.concept_id} references missing sources: {sorted(unknown_sources)}")
            if unknown_relations:
                raise ValueError(f"{concept.concept_id} references missing concepts: {sorted(unknown_relations)}")
        for strategy in self._strategies.values():
            required_scalars = (
                strategy.strategy_id, strategy.name, strategy.strategy_version, strategy.updated_at,
                strategy.example, strategy.common_failure, strategy.evidence_status,
            )
            required_lists = (
                strategy.schools, strategy.concept_ids, strategy.inputs, strategy.setup_rules,
                strategy.confirmation_rules, strategy.invalidation_rules, strategy.no_trade_conditions, strategy.source_ids,
            )
            if not self._all_text(required_scalars) or any(not values or not self._all_text(values) for values in required_lists):
                raise ValueError(f"{strategy.strategy_id} has empty structured strategy fields.")
            unknown_concepts = set(strategy.concept_ids) - set(self._concepts)
            unknown_sources = set(strategy.source_ids) - set(self._sources)
            if unknown_concepts:
                raise ValueError(f"{strategy.strategy_id} references missing concepts: {sorted(unknown_concepts)}")
            if unknown_sources:
                raise ValueError(f"{strategy.strategy_id} references missing sources: {sorted(unknown_sources)}")

    def get(self, concept_id: str) -> KnowledgeConcept:
        try:
            return self._concepts[concept_id]
        except KeyError as exc:
            raise KeyError(f"Unknown concept: {concept_id}") from exc

    def source(self, source_id: str) -> KnowledgeSource:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown knowledge source: {source_id}") from exc

    def sources_for(self, concept_id: str) -> tuple[KnowledgeSource, ...]:
        return tuple(self.source(source_id) for source_id in self.get(concept_id).source_ids)

    @staticmethod
    def _corpus(concept: KnowledgeConcept) -> dict[str, str]:
        return {
            "identity": " ".join((concept.concept_id, concept.school, concept.name)),
            "rule": " ".join((concept.operational_rule, *concept.inputs, concept.signal_validity, concept.invalidation)),
            "use_case": " ".join(concept.use_cases),
            "examples": " ".join((*concept.examples, *concept.common_failures)),
            "evidence": " ".join((concept.evidence_notes, concept.evidence_status, *concept.tags)),
        }

    def search(self, text: str = "", *, school: str | None = None, requires: str | None = None) -> tuple[KnowledgeConcept, ...]:
        """Return deterministic exact-term matches, preserving school/input boundaries."""
        terms = text.casefold().split()
        matches = []
        for concept in self._concepts.values():
            if school and concept.school.casefold() != school.casefold():
                continue
            if requires and not any(requires.casefold() in item.casefold() for item in concept.inputs):
                continue
            corpus = " ".join(self._corpus(concept).values()).casefold()
            if all(term in corpus for term in terms):
                matches.append(concept)
        return tuple(sorted(matches, key=lambda item: item.concept_id))

    def retrieve(
        self,
        query: str = "",
        *,
        school: str | None = None,
        use_case: str | None = None,
        requires_all: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[KnowledgeRetrieval, ...]:
        """Rank appropriate documented concepts with transparent field-level matches.

        This is purposely not semantic/vector retrieval: all relevance scores are based
        on exact case-folded query terms so a report can state why a concept was offered.
        ``requires_all`` filters against declared inputs before ranking.
        """
        if limit < 1:
            raise ValueError("Knowledge retrieval limit must be at least one.")
        terms = tuple(term for term in query.casefold().split() if term)
        required = tuple(value.casefold() for value in requires_all if value.strip())
        results: list[KnowledgeRetrieval] = []
        weights = {"identity": 5, "rule": 3, "use_case": 3, "examples": 1, "evidence": 2}
        for concept in self._concepts.values():
            if school and concept.school.casefold() != school.casefold():
                continue
            if use_case and use_case.casefold() not in " ".join(concept.use_cases).casefold():
                continue
            declared_inputs = " ".join(concept.inputs).casefold()
            if any(value not in declared_inputs for value in required):
                continue
            fields = self._corpus(concept)
            matched_fields: list[str] = []
            score = 0
            for field, value in fields.items():
                normalized = value.casefold()
                matches = sum(term in normalized for term in terms)
                if matches:
                    matched_fields.append(field)
                    score += weights[field] * matches
            if terms and not score:
                continue
            # Browsing with filters but no query is deterministic and intentionally low score.
            results.append(KnowledgeRetrieval(concept, score, tuple(matched_fields)))
        return tuple(sorted(results, key=lambda item: (-item.score, item.concept.concept_id))[:limit])

    def strategy(self, strategy_id: str) -> KnowledgeStrategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise KeyError(f"Unknown knowledge strategy: {strategy_id}") from exc

    def strategies(self, *, school: str | None = None) -> tuple[KnowledgeStrategy, ...]:
        values = (
            strategy for strategy in self._strategies.values()
            if school is None or any(value.casefold() == school.casefold() for value in strategy.schools)
        )
        return tuple(sorted(values, key=lambda strategy: strategy.strategy_id))

    def retrieve_strategies(self, query: str = "", *, school: str | None = None, limit: int = 10) -> tuple[StrategyRetrieval, ...]:
        """Retrieve versioned strategy templates with the same explainable exact-term rule."""
        if limit < 1:
            raise ValueError("Strategy retrieval limit must be at least one.")
        terms = tuple(term for term in query.casefold().split() if term)
        results: list[StrategyRetrieval] = []
        for strategy in self.strategies(school=school):
            fields = {
                "identity": " ".join((strategy.strategy_id, strategy.name, *strategy.schools)),
                "rules": " ".join((*strategy.inputs, *strategy.setup_rules, *strategy.confirmation_rules, *strategy.invalidation_rules, *strategy.no_trade_conditions)),
                "review": " ".join((strategy.example, strategy.common_failure, strategy.evidence_status)),
            }
            weights = {"identity": 5, "rules": 3, "review": 1}
            matched = tuple(field for field, value in fields.items() if any(term in value.casefold() for term in terms))
            score = sum(weights[field] * sum(term in fields[field].casefold() for term in terms) for field in matched)
            if terms and not score:
                continue
            results.append(StrategyRetrieval(strategy, score, matched))
        return tuple(sorted(results, key=lambda item: (-item.score, item.strategy.strategy_id))[:limit])

    def related(self, concept_id: str) -> tuple[KnowledgeConcept, ...]:
        return tuple(self.get(related_id) for related_id in self.get(concept_id).relations)
