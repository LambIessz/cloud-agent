"""核心 Agent 框架组件。"""

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

from .mcp.mcp_manager import MCPManager
from .workflow.state import AgentOutput, AgentState
from .memory.memory_manager import MemoryManager

__all__ = ["AgentOutput", "AgentState", "MCPManager", "MemoryManager"]
