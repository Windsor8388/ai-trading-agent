"""Multi-timeframe coordination: higher frames provide context; lower frames provide timing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .engines.base import AnalysisEngine
from .models import EngineReport, MarketSnapshot


TIMEFRAME_ROLES: dict[str, str] = {
    "MN": "structural_context", "W1": "structural_context", "D1": "structural_context",
    "H4": "trend_context", "H1": "setup", "M30": "setup",
    "M15": "execution", "M5": "execution", "M1": "execution",
}


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    reports: tuple[EngineReport, ...]
    skipped: tuple[str, ...]


class AnalysisOrchestrator:
    """Runs independent engines without allowing one engine to mutate another's inputs."""

    def __init__(self, engines: Sequence[AnalysisEngine]) -> None:
        self._engines = tuple(engines)

    def run(self, snapshot: MarketSnapshot, timeframes: Iterable[str]) -> AnalysisRun:
        reports: list[EngineReport] = []
        skipped: list[str] = []
        for timeframe in timeframes:
            if not snapshot.candles(timeframe):
                skipped.append(f"{timeframe}: no bars supplied")
                continue
            for engine in self._engines:
                reports.append(engine.analyze(snapshot, timeframe))
        return AnalysisRun(tuple(reports), tuple(skipped))
