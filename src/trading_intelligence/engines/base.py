from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import EngineReport, MarketSnapshot


@dataclass(frozen=True, slots=True)
class AnalysisEngine(ABC):
    """An engine owns only its documented concepts and receives immutable snapshots."""

    name: str
    version: str

    @abstractmethod
    def analyze(self, snapshot: MarketSnapshot, timeframe: str) -> EngineReport:
        raise NotImplementedError
