"""Analysis-first, auditable trading intelligence primitives.

This package deliberately contains no broker credentials, order-routing code, or live
execution capability. It is an initial research prototype, not financial advice.
"""

from .knowledge import KnowledgeBase
from .models import Candle, DecisionAction, MarketSnapshot
from .monitoring import OperationalMonitor
from .risk import RiskManagementEngine
from .risk_book import PortfolioRiskBook

__all__ = ["Candle", "DecisionAction", "KnowledgeBase", "MarketSnapshot", "OperationalMonitor", "PortfolioRiskBook", "RiskManagementEngine"]
__version__ = "0.3.0"
