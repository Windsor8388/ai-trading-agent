"""Analysis-first, auditable trading intelligence primitives.

This package deliberately contains no broker credentials, order-routing code, or live
execution capability. It is an initial research prototype, not financial advice.
"""

from .agents import MarketResearchAgent
from .integrations import GeminiRole, LiveIntegrationGate
from .knowledge import KnowledgeBase
from .models import Candle, DecisionAction, MarketSnapshot, SignalCondition
from .monitoring import OperationalMonitor
from .risk import RiskManagementEngine
from .risk_book import PortfolioRiskBook

__all__ = [
    "Candle", "DecisionAction", "GeminiRole", "KnowledgeBase", "LiveIntegrationGate", "MarketResearchAgent",
    "MarketSnapshot", "OperationalMonitor", "PortfolioRiskBook", "RiskManagementEngine", "SignalCondition",
]
__version__ = "0.9.0"
