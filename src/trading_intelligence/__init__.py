"""Analysis-first, auditable trading intelligence primitives.

This package deliberately contains no configured broker credentials or production-live
order-routing capability. It includes guarded server-side VST validation adapters only;
this remains an initial research prototype, not financial advice.
"""

from .agents import ChartAnalysisAgent, MarketResearchAgent
from .integrations import GeminiRole, LiveIntegrationGate
from .knowledge import KnowledgeBase
from .models import Candle, DecisionAction, MarketSnapshot, SignalCondition
from .monitoring import OperationalMonitor
from .risk import RiskManagementEngine
from .risk_book import PortfolioRiskBook

__all__ = [
    "Candle", "ChartAnalysisAgent", "DecisionAction", "GeminiRole", "KnowledgeBase", "LiveIntegrationGate", "MarketResearchAgent",
    "MarketSnapshot", "OperationalMonitor", "PortfolioRiskBook", "RiskManagementEngine", "SignalCondition",
]
__version__ = "0.11.0"
