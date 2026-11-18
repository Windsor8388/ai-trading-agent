"""Read-only market-data contracts, adapters, and quality-gated ingestion coordination.

No provider credentials or active live connections are included in this package.
"""
from .contracts import BarRequest, EconomicCalendarProvider, EconomicEvent, MarketDataProvider, MarketDataResponse
from .csv_ohlcv import CSVBarSpec, CSVImportResult, import_ohlcv_csv
from .instruments import AssetClass, InstrumentDefinition, InstrumentRegistry, default_instrument_registry
from .oanda_v20 import JsonHttpTransport, OandaV20Config, OandaV20MarketDataProvider, UrllibJsonTransport
from .pipeline import (
    DataAnomaly,
    DataDuplicate,
    DataGap,
    DataPipelinePolicy,
    IngestionReceipt,
    MarketDataPipeline,
    PeriodicPollingCoordinator,
    PipelineQuality,
    PollSchedule,
    inspect_gaps_and_anomalies,
)

__all__ = [
    "AssetClass", "BarRequest", "CSVBarSpec", "CSVImportResult", "DataAnomaly", "DataDuplicate", "DataGap",
    "DataPipelinePolicy", "EconomicCalendarProvider", "EconomicEvent", "IngestionReceipt", "InstrumentDefinition",
    "InstrumentRegistry", "JsonHttpTransport", "MarketDataPipeline", "MarketDataProvider", "MarketDataResponse",
    "OandaV20Config", "OandaV20MarketDataProvider", "PeriodicPollingCoordinator", "PipelineQuality", "PollSchedule",
    "UrllibJsonTransport", "default_instrument_registry", "import_ohlcv_csv", "inspect_gaps_and_anomalies",
]
