"""Evidence de-correlation and conservative, explainable central decisioning."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import Decision, DecisionAction, Direction, EngineReport, Evidence, MarketRegime


@dataclass(frozen=True, slots=True)
class CentralPolicy:
    min_directional_score: float = 0.45
    min_independent_groups: int = 2
    block_on_higher_timeframe_conflict: bool = True
    higher_timeframes: tuple[str, ...] = ("MN", "W1", "D1", "H4")


class CentralTradingIntelligenceEngine:
    """Combines observations, not brand names.

    Each correlation group contributes at most one observation. ICT, SMC, and
    price-action structural claims deliberately share `market_structure`, so agreeing
    descriptions of the same swing are not counted three times.
    """

    version = "0.1.0"

    def __init__(self, policy: CentralPolicy | None = None) -> None:
        self.policy = policy or CentralPolicy()

    @staticmethod
    def _regime(reports: Sequence[EngineReport]) -> MarketRegime:
        weighted: dict[MarketRegime, float] = defaultdict(float)
        for report in reports:
            if report.regime != MarketRegime.UNKNOWN:
                weighted[report.regime] += report.confidence
        return max(weighted, key=weighted.get) if weighted else MarketRegime.UNKNOWN

    def decide(self, reports: Iterable[EngineReport]) -> Decision:
        report_list = list(reports)
        now = max((report.as_of for report in report_list), default=None)
        if now is None:
            raise ValueError("Cannot decide without engine reports.")
        all_evidence = [item for report in report_list for item in (*report.evidence, *report.counter_evidence)]
        if not all_evidence:
            return Decision(DecisionAction.NO_TRADE, 0.0, now, (), (),
                            ("NO TRADE: no measurable directional evidence.",), (),
                            tuple(condition for report in report_list for condition in report.invalidation_conditions), self._regime(report_list))
        by_group: dict[str, list[Evidence]] = defaultdict(list)
        for evidence in all_evidence:
            by_group[evidence.correlation_group].append(evidence)
        selected: list[Evidence] = []
        discarded: list[Evidence] = []
        for group_items in by_group.values():
            # Product gives weaker observations less power and preserves direction.
            winner = max(group_items, key=lambda item: abs(item.score) * item.confidence)
            selected.append(winner)
            discarded.extend(item for item in group_items if item is not winner)
        total_weight = sum(item.confidence for item in selected)
        aggregate = sum(item.score * item.confidence for item in selected) / total_weight if total_weight else 0.0
        intended = Direction.BUY if aggregate > 0 else Direction.SELL if aggregate < 0 else Direction.FLAT
        support = sum(1 for item in selected if item.score * aggregate > 0 and abs(item.score) >= 0.35)
        opposition = [item for item in selected if item.score * aggregate < 0]
        reasons = [
            f"{item.engine}/{item.timeframe}: {item.detail}"
            for item in selected if item.score * aggregate > 0
        ]
        against = [f"{item.engine}/{item.timeframe}: {item.detail}" for item in opposition]
        invalidations = tuple(dict.fromkeys(condition for report in report_list for condition in report.invalidation_conditions))
        higher_frame_directions = {
            report.proposed_direction for report in report_list
            if report.timeframe in self.policy.higher_timeframes and report.proposed_direction != Direction.FLAT and report.confidence >= 0.4
        }
        if self.policy.block_on_higher_timeframe_conflict and intended != Direction.FLAT:
            opposite = Direction.SELL if intended == Direction.BUY else Direction.BUY
            if opposite in higher_frame_directions:
                return Decision(DecisionAction.NO_TRADE, abs(aggregate), now, tuple(selected), tuple(discarded),
                                ("NO TRADE: higher-timeframe context conflicts with proposed direction.", *reasons), tuple(against), invalidations, self._regime(report_list))
        if abs(aggregate) < self.policy.min_directional_score:
            return Decision(DecisionAction.NO_TRADE, abs(aggregate), now, tuple(selected), tuple(discarded),
                            (f"NO TRADE: de-correlated score {aggregate:.3f} is below {self.policy.min_directional_score:.3f}.", *reasons), tuple(against), invalidations, self._regime(report_list))
        if support < self.policy.min_independent_groups:
            return Decision(DecisionAction.NO_TRADE, abs(aggregate), now, tuple(selected), tuple(discarded),
                            (f"NO TRADE: only {support} independent evidence group(s); policy requires {self.policy.min_independent_groups}.", *reasons), tuple(against), invalidations, self._regime(report_list))
        action = DecisionAction.BUY if intended == Direction.BUY else DecisionAction.SELL
        return Decision(action, min(1.0, abs(aggregate)), now, tuple(selected), tuple(discarded), tuple(reasons), tuple(against), invalidations, self._regime(report_list))
