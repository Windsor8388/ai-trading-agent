"""Append-only, hash-chained research and prediction ledger.

The ledger is intentionally local JSONL so a prototype can record what was known before
an outcome without needing a database. It detects accidental or manual edits; a future
production implementation should additionally use immutable object storage/access logs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    sequence: int
    recorded_at: str
    category: str
    payload: Mapping[str, Any]
    parent_hash: str | None
    entry_hash: str


class ResearchLedger:
    version = "0.1.0"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if is_dataclass(value):
            return ResearchLedger._json_safe(asdict(value))
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Ledger timestamps must be timezone-aware.")
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, Mapping):
            return {str(key): ResearchLedger._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ResearchLedger._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        raise TypeError(f"Payload value is not JSON-safe: {type(value).__name__}")

    @staticmethod
    def _hash_body(sequence: int, recorded_at: str, category: str, payload: Mapping[str, Any], parent_hash: str | None) -> str:
        canonical = json.dumps({"sequence": sequence, "recorded_at": recorded_at, "category": category, "payload": payload, "parent_hash": parent_hash}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def entries(self) -> tuple[LedgerEntry, ...]:
        if not self.path.exists():
            return ()
        records: list[LedgerEntry] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"Ledger contains blank line at {line_number}; append-only format violated.")
                try:
                    raw = json.loads(line)
                    entry = LedgerEntry(raw["sequence"], raw["recorded_at"], raw["category"], raw["payload"], raw["parent_hash"], raw["entry_hash"])
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise ValueError(f"Invalid ledger record at line {line_number}") from exc
                records.append(entry)
        return tuple(records)

    def verify(self) -> tuple[LedgerEntry, ...]:
        records = self.entries()
        previous_hash: str | None = None
        for expected_sequence, entry in enumerate(records, start=1):
            if entry.sequence != expected_sequence:
                raise ValueError(f"Ledger sequence mismatch at entry {expected_sequence}.")
            if entry.parent_hash != previous_hash:
                raise ValueError(f"Ledger parent hash mismatch at entry {expected_sequence}.")
            calculated = self._hash_body(entry.sequence, entry.recorded_at, entry.category, entry.payload, entry.parent_hash)
            if calculated != entry.entry_hash:
                raise ValueError(f"Ledger hash mismatch at entry {expected_sequence}; record may have been altered.")
            previous_hash = entry.entry_hash
        return records

    def append(self, category: str, payload: Mapping[str, Any], *, recorded_at: datetime | None = None) -> LedgerEntry:
        if not category or category.strip() != category:
            raise ValueError("category must be a non-empty trimmed string.")
        existing = self.verify()
        timestamp = recorded_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware.")
        safe_payload = self._json_safe(payload)
        sequence = len(existing) + 1
        parent_hash = existing[-1].entry_hash if existing else None
        iso_time = timestamp.astimezone(timezone.utc).isoformat()
        entry_hash = self._hash_body(sequence, iso_time, category, safe_payload, parent_hash)
        entry = LedgerEntry(sequence, iso_time, category, safe_payload, parent_hash, entry_hash)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
        return entry
