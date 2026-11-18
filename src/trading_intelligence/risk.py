"""Capital-protection gate, intentionally independent from directional engines."""
from __future__ import annotations

from math import floor

from .models import DecisionAction, InstrumentRiskSpec, PortfolioState, ProposedLevels, RiskAssessment, RiskLimits


class RiskManagementEngine:
    version = "0.2.0"

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def risk_per_unit(self, entry: float, stop_loss: float, spec: InstrumentRiskSpec) -> float:
        """Worst-case stop distance after the configured gap buffer, before commissions.

        The buffer is a policy input, not a claim that it models every gap. A live
        venue implementation must still reconcile actual fills and slippage.
        """
        stop_distance = abs(entry - stop_loss)
        return stop_distance * (1 + self.limits.gap_buffer_fraction) * spec.contract_multiplier

    def assess(self, action: DecisionAction, levels: ProposedLevels | None, portfolio: PortfolioState, spec: InstrumentRiskSpec | None) -> RiskAssessment:
        reasons: list[str] = []
        if action not in (DecisionAction.BUY, DecisionAction.SELL):
            return RiskAssessment(False, None, None, ("No directional decision to size.",))
        if levels is None:
            return RiskAssessment(False, None, None, ("Entry, stop, and target levels are required before sizing.",))
        if spec is None:
            return RiskAssessment(False, None, None, ("Broker/product-specific contract multiplier is required; it is never inferred.",))
        if portfolio.equity <= 0:
            return RiskAssessment(False, None, None, ("Equity must be positive.",))
        if (action == DecisionAction.BUY and levels.stop_loss >= levels.entry) or (action == DecisionAction.SELL and levels.stop_loss <= levels.entry):
            return RiskAssessment(False, None, None, ("Stop-loss is on the wrong side of entry.",))
        if not levels.targets:
            return RiskAssessment(False, None, None, ("At least one target is required to evaluate reward/risk.",))
        risk_per_unit = self.risk_per_unit(levels.entry, levels.stop_loss, spec)
        reward = (levels.targets[0] - levels.entry) if action == DecisionAction.BUY else (levels.entry - levels.targets[0])
        if reward <= 0:
            return RiskAssessment(False, None, None, ("First target is not on the reward side of entry.",))
        reward_to_risk = reward / (risk_per_unit / spec.contract_multiplier)
        if reward_to_risk < self.limits.min_reward_to_risk:
            reasons.append(f"Reward/risk {reward_to_risk:.2f} is below the configured minimum {self.limits.min_reward_to_risk:.2f}.")
        if portfolio.realized_daily_loss >= portfolio.equity * self.limits.daily_loss_fraction:
            reasons.append("Daily loss limit reached; trading is stopped.")
        if portfolio.realized_weekly_loss >= portfolio.equity * self.limits.weekly_loss_fraction:
            reasons.append("Weekly loss limit reached; trading is stopped.")
        if portfolio.drawdown_fraction >= self.limits.max_drawdown_fraction:
            reasons.append("Maximum drawdown limit reached; trading is stopped.")
        if portfolio.open_positions >= self.limits.max_open_positions:
            reasons.append("Maximum open-position count reached.")
        if portfolio.correlated_open_positions >= self.limits.max_correlated_positions:
            reasons.append("Correlated-position limit reached.")
        if portfolio.consecutive_losses >= self.limits.max_consecutive_losses:
            reasons.append("Consecutive-loss circuit breaker reached; trading is stopped.")
        if portfolio.gross_exposure_fraction >= self.limits.max_gross_exposure_fraction:
            reasons.append("Gross-exposure limit reached.")
        if portfolio.event_risk_active:
            reasons.append("Configured event-risk embargo is active; new entries are blocked.")
        if portfolio.gap_risk_active:
            reasons.append("Configured gap-risk embargo is active; new entries are blocked.")
        if reasons:
            return RiskAssessment(False, None, None, tuple(reasons), reward_to_risk, risk_per_unit)
        cash_risk = portfolio.equity * self.limits.risk_per_trade_fraction
        raw_quantity = cash_risk / risk_per_unit
        quantity = floor(raw_quantity / spec.quantity_step) * spec.quantity_step
        if quantity < spec.minimum_quantity:
            return RiskAssessment(False, None, cash_risk, ("Risk budget is too small for the instrument minimum quantity.",), reward_to_risk, risk_per_unit)
        worst_case_cash_risk = quantity * risk_per_unit
        return RiskAssessment(
            True, quantity, cash_risk,
            (f"Sized at <= {self.limits.risk_per_trade_fraction:.2%} equity risk with a {self.limits.gap_buffer_fraction:.2%} stop-gap buffer; commissions/slippage remain separate assumptions.",),
            reward_to_risk, risk_per_unit, worst_case_cash_risk,
        )
