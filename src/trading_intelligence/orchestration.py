"""Multi-timeframe coordination: higher frames provide context; lower frames provide timing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from .engines.base import AnalysisEngine
from .models import EngineReport, MarketSnapshot
from .research.ledger import LedgerEntry, ResearchLedger


TIMEFRAME_ROLES: dict[str, str] = {
    "MN": "structural_context", "W1": "structural_context", "D1": "structural_context",
    "H4": "trend_context", "H1": "setup", "M30": "setup",
    "M15": "execution", "M5": "execution", "M1": "execution",
}


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    reports: tuple[EngineReport, ...]
    skipped: tuple[str, ...]
    as_of: datetime | None = None


class AnalysisReportRecorder:
    """Append independent engine outputs to a hash-chained research ledger.

    Recording is explicit so in-memory exploration stays possible, while a paper or
    forward-research caller can prove which reports existed at a particular ``as_of``.
    The recorder stores observations and missing inputs as well as positive evidence;
    a no-result report must not disappear from the audit trail.
    """

    version = "0.1.0"

    @staticmethod
    def record(run: AnalysisRun, ledger: ResearchLedger, *, run_id: str) -> tuple[LedgerEntry, ...]:
        if not run_id or run_id.strip() != run_id:
            raise ValueError("run_id must be a non-empty trimmed string.")
        entries: list[LedgerEntry] = []
        for report in run.reports:
            entries.append(ledger.append(
                "analysis.engine_report",
                {
                    "run_id": run_id,
                    "engine": report.engine,
                    "timeframe": report.timeframe,
                    "as_of": report.as_of,
                    "report": report,
                },
                recorded_at=report.as_of,
            ))
        if run.skipped and run.as_of is not None:
            entries.append(ledger.append(
                "analysis.skipped_timeframes",
                {"run_id": run_id, "skipped": run.skipped},
                recorded_at=run.as_of,
            ))
        return tuple(entries)


class AnalysisOrchestrator:
    """Run independent engines without allowing one engine to mutate another's inputs."""

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
        return AnalysisRun(tuple(reports), tuple(skipped), snapshot.as_of)

    def run_and_record(
        self,
        snapshot: MarketSnapshot,
        timeframes: Iterable[str],
        *,
        ledger: ResearchLedger,
        run_id: str,
    ) -> AnalysisRun:
        """Run then append each report in execution order to the local hash-chained ledger."""
        run = self.run(snapshot, timeframes)
        AnalysisReportRecorder.record(run, ledger, run_id=run_id)
        return run
