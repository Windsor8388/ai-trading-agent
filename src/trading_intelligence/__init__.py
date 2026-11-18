"""Analysis-first, auditable trading intelligence primitives.

This package deliberately contains no broker credentials, order-routing code, or live
execution capability. It is an initial research prototype, not financial advice.
"""

from .knowledge import KnowledgeBase
from .models import DecisionAction, MarketSnapshot, Candle

__all__ = ["Candle", "DecisionAction", "KnowledgeBase", "MarketSnapshot"]
__version__ = "0.1.0"
