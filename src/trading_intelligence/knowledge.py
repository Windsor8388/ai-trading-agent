"""Versioned local knowledge-base reader with source-reference integrity checks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class KnowledgeConcept:
    concept_id: str
    school: str
    name: str
    operational_rule: str
    inputs: tuple[str, ...]
    invalidation: str
    relations: tuple[str, ...]
    evidence_status: str
    source_ids: tuple[str, ...]


class KnowledgeBase:
    """Read-only concept retrieval for analysis/reporting.

    Runtime rules remain versioned code; this registry supplies their provenance,
    relationships, documented assumptions, and missing-input requirements. Loading
    fails fast if any concept points to a missing source or relation.
    """

    def __init__(self, concepts: Iterable[KnowledgeConcept], source_ids: Iterable[str], version: str, updated_at: str) -> None:
        self._concepts = {concept.concept_id: concept for concept in concepts}
        self._source_ids = frozenset(source_ids)
        self.version = version
        self.updated_at = updated_at
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
        concepts = [
            KnowledgeConcept(
                concept_id=item["id"], school=item["school"], name=item["name"],
                operational_rule=item["operational_rule"], inputs=tuple(item["inputs"]),
                invalidation=item["invalidation"], relations=tuple(item["relations"]),
                evidence_status=item["evidence_status"], source_ids=tuple(item["source_ids"]),
            )
            for item in concept_document["concepts"]
        ]
        return cls(concepts, (item["id"] for item in source_document["sources"]), concept_document["schema_version"], concept_document["updated_at"])

    def _validate(self) -> None:
        if not self._concepts:
            raise ValueError("Knowledge base has no concepts.")
        for concept in self._concepts.values():
            unknown_sources = set(concept.source_ids) - self._source_ids
            unknown_relations = set(concept.relations) - set(self._concepts)
            if unknown_sources:
                raise ValueError(f"{concept.concept_id} references missing sources: {sorted(unknown_sources)}")
            if unknown_relations:
                raise ValueError(f"{concept.concept_id} references missing concepts: {sorted(unknown_relations)}")

    def get(self, concept_id: str) -> KnowledgeConcept:
        try:
            return self._concepts[concept_id]
        except KeyError as exc:
            raise KeyError(f"Unknown concept: {concept_id}") from exc

    def search(self, text: str = "", *, school: str | None = None, requires: str | None = None) -> tuple[KnowledgeConcept, ...]:
        """Simple deterministic retrieval. Semantic/vector retrieval is intentionally deferred.

        Query results preserve concept IDs and sources; an LLM must not replace this
        canonical registry with an unversioned paraphrase.
        """
        terms = text.casefold().split()
        matches = []
        for concept in self._concepts.values():
            if school and concept.school.casefold() != school.casefold():
                continue
            if requires and not any(requires.casefold() in item.casefold() for item in concept.inputs):
                continue
            corpus = " ".join((concept.concept_id, concept.school, concept.name, concept.operational_rule, *concept.inputs)).casefold()
            if all(term in corpus for term in terms):
                matches.append(concept)
        return tuple(sorted(matches, key=lambda item: item.concept_id))

    def related(self, concept_id: str) -> tuple[KnowledgeConcept, ...]:
        return tuple(self.get(related_id) for related_id in self.get(concept_id).relations)
