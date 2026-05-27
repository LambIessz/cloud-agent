"""Agent implementations."""

import warnings

try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
except ImportError:  # pragma: no cover - compatibility with older langchain-core
    LangChainPendingDeprecationWarning = PendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change in a future version.*",
    category=LangChainPendingDeprecationWarning,
)

from .orchestrator import OrchestratorAgent
from .product_agent import ProductAgentNode
from .billing_agent import BillingAgentNode
from .fallback_agent import FallbackAgentNode
from .promotion_agent import PromotionAgentNode
from .recommendation_agent import RecommendationAgent
from .support_agent import SupportAgentNode

__all__ = [
    "OrchestratorAgent",
    "ProductAgentNode",
    "BillingAgentNode",
    "FallbackAgentNode",
    "PromotionAgentNode",
    "RecommendationAgent",
    "SupportAgentNode",
]
