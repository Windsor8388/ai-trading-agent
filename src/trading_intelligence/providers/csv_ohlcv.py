"""Strict, reproducible import of user-supplied OHLCV CSV files.

This is an offline adapter, not a claim that the file is reliable market data. It records
an SHA-256 fingerprint and refuses malformed rows rather than silently repairing them.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..data import DataQualityReport, timeframe_seconds, validate_closed_candles
from ..models import Candle, DataMode, FeedMetadata


@dataclass(frozen=True, slots=True)
class CSVBarSpec:
    path: Path
    timeframe: str
    provider: str
    instrument: str
    instrument_kind: str
    price_component: str
    timestamp_column: str = "timestamp"
    open_column: str = "open"
    high_column: str = "high"
    low_column: str = "low"
    close_column: str = "close"
    volume_column: str | None = "volume"
    timestamp_timezone: str = "UTC"
    timestamp_is_close: bool = False
    vendor_symbol: str | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_sha256: str
    source_filename: str
    parser_version: str
    rows: int
    first_open_time: datetime
    last_close_time: datetime
    feed: FeedMetadata
    quality: DataQualityReport


@dataclass(frozen=True, slots=True)
class CSVImportResult:
    candles: tuple[Candle, ...]
    manifest: DatasetManifest


def _parse_timestamp(value: str, timezone_name: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception as exc:  # ZoneInfo exposes distinct errors across Python builds.
            raise ValueError(f"Invalid timestamp timezone {timezone_name!r}") from exc
    return parsed.astimezone(ZoneInfo("UTC"))


def _required_columns(spec: CSVBarSpec) -> set[str]:
    columns = {spec.timestamp_column, spec.open_column, spec.high_column, spec.low_column, spec.close_column}
    if spec.volume_column:
        columns.add(spec.volume_column)
    return columns


def import_ohlcv_csv(spec: CSVBarSpec, *, retrieved_at: datetime | None = None) -> CSVImportResult:
    """Read a complete OHLCV CSV into an immutable, fingerprinted historical dataset.

    Timestamps are ISO-8601. Naive timestamps are accepted only when the caller states
    their source timezone; they are converted to UTC before validation. Every row is
    all-or-nothing: a malformed numeric/temporal/OHLC row raises with its line number.
    """
    path = Path(spec.path)
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    duration = timedelta(seconds=timeframe_seconds(spec.timeframe))
    rows: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row.")
        missing = _required_columns(spec) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = _parse_timestamp(row[spec.timestamp_column], spec.timestamp_timezone)
                open_time, close_time = (timestamp - duration, timestamp) if spec.timestamp_is_close else (timestamp, timestamp + duration)
                volume = float(row[spec.volume_column]) if spec.volume_column and row[spec.volume_column] not in (None, "") else None
                rows.append(Candle(
                    open_time, close_time, float(row[spec.open_column]), float(row[spec.high_column]),
                    float(row[spec.low_column]), float(row[spec.close_column]), volume,
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid CSV row {line_number}: {exc}") from exc
    if not rows:
        raise ValueError("CSV contains no data rows.")
    as_of = rows[-1].close_time
    quality = validate_closed_candles(rows, as_of)
    if not quality.passed:
        raise ValueError(f"Imported CSV failed data quality: {'; '.join(quality.errors)}")
    received = retrieved_at or datetime.now(tz=ZoneInfo("UTC"))
    if received.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware.")
    feed = FeedMetadata(
        provider=spec.provider, instrument=spec.instrument, instrument_kind=spec.instrument_kind,
        price_component=spec.price_component, mode=DataMode.HISTORICAL, retrieved_at=received,
        vendor_symbol=spec.vendor_symbol, source_url=spec.source_url, quality_status="PASS" if not quality.warnings else "PASS_WITH_WARNINGS",
        notes=f"CSV SHA-256={fingerprint}; parser=csv_ohlcv/0.1.0",
    )
    manifest = DatasetManifest(fingerprint, path.name, "csv_ohlcv/0.1.0", len(rows), rows[0].open_time, rows[-1].close_time, feed, quality)
    return CSVImportResult(tuple(rows), manifest)
