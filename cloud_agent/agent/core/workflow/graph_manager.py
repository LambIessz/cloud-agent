import os
import sys
from pathlib import Path
# 这里没有 sys.path.insert！我们期望调用者 (main.py) 正确设置 sys.path。

import asyncio
import warnings
from typing import Literal

try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
except ImportError:  # pragma: no cover - compatibility with older langchain-core
    LangChainPendingDeprecationWarning = PendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change in a future version.*",
    category=LangChainPendingDeprecationWarning,
)

from langgraph.graph import StateGraph, START, END
from core.workflow.state import AgentState
from agents.orchestrator import OrchestratorAgent
from agents.product_agent import ProductAgentNode
from agents.billing_agent import BillingAgentNode
from agents.fallback_agent import FallbackAgentNode
from agents.promotion_agent import PromotionAgentNode
from agents.recommendation_agent import RecommendationAgent
from agents.checkpoint_agent import CheckpointAgentNode
from agents.finops_agent import FinOpsAgentNode
from agents.support_agent import SupportAgentNode
from agents.planner_agent import TaskPlannerNode
from agents.collaboration_agent import CollaborationSynthesisAgent

class AgentGraphManager:
    """
    负责组装 LangGraph 多 Agent 编排。
    支持 FinOps 工作流的跨 Agent 协同状态交接 (State Handoff)。
    """
    def __init__(
        self,
        *,
        support_node: SupportAgentNode | None = None,
        checkpoint_node: CheckpointAgentNode | None = None,
        planner_node: TaskPlannerNode | None = None,
        collaboration_node: CollaborationSynthesisAgent | None = None,
    ):
        self.orchestrator = OrchestratorAgent()
        self.product_node = ProductAgentNode()
        self.billing_node = BillingAgentNode()
        self.fallback_node = FallbackAgentNode()
        self.promotion_node = PromotionAgentNode()
        self.recommendation_node = RecommendationAgent()
        self.finops_node = FinOpsAgentNode()
        self.support_node = support_node or SupportAgentNode()
        self.checkpoint_node = checkpoint_node or CheckpointAgentNode()
        self.planner_node = planner_node or TaskPlannerNode()
        self.collaboration_node = collaboration_node or CollaborationSynthesisAgent()

    def _route_condition(self, state: AgentState) -> str:
        """根据 Orchestrator 的决策决定走向哪个 Agent 节点。"""
        return state.get("next_agent") or "fallback_agent"

    def _billing_post_condition(self, state: AgentState) -> str:
        """
        BillingAgent 节点执行完后的条件判断：
        如果是在执行 FinOps 工作流，就把接力棒交给 FinOps Agent；
        如果是普通账单查询，直接结束。
        """
        if state.get("metadata", {}).get("is_finops_workflow"):
            return "finops_agent"
        return END

    def _finops_post_condition(self, state: AgentState) -> str:
        collaboration_state = state.get("metadata", {}).get("collaboration_state")
        if isinstance(collaboration_state, dict):
            status = str(collaboration_state.get("status") or "").lower()
            findings = collaboration_state.get("findings")
            mode = str(collaboration_state.get("mode") or "").lower()
            if (
                mode == "billing_finops_synthesis"
                and status != "merged"
                and isinstance(findings, list)
                and len(findings) >= 2
            ):
                return "collaboration_agent"
        return END

    def _checkpoint_post_condition(self, state: AgentState) -> str:
        checkpoint = state.get("metadata", {}).get("human_checkpoint")
        if not isinstance(checkpoint, dict):
            return END

        status = str(checkpoint.get("status") or "").lower()
        if status == "confirmed":
            return str(checkpoint.get("resume_agent") or "support_agent")
        return END

    def build_graph(self) -> StateGraph:
        """构建状态图"""
        builder = StateGraph(AgentState)

        # 1. 添加节点
        builder.add_node("orchestrator", self.orchestrator.route)
        builder.add_node("product_agent", self.product_node)
        builder.add_node("billing_agent", self.billing_node)
        builder.add_node("fallback_agent", self.fallback_node)
        builder.add_node("promotion_agent", self.promotion_node)
        builder.add_node("recommendation_agent", self.recommendation_node)
        builder.add_node("finops_agent", self.finops_node)
        builder.add_node("support_agent", self.support_node)
        builder.add_node("checkpoint_agent", self.checkpoint_node)
        builder.add_node("planner_agent", self.planner_node)
        builder.add_node("collaboration_agent", self.collaboration_node)

        # 2. 定义边
        builder.add_edge(START, "orchestrator")

        # Orchestrator 之后，根据 condition 路由到不同的基础 Agent
        builder.add_conditional_edges(
            "orchestrator",
            self._route_condition,
            {
                "product_agent": "product_agent",
                "billing_agent": "billing_agent",
                "fallback_agent": "fallback_agent",
                "promotion_agent": "promotion_agent",
                "recommendation_agent": "recommendation_agent",
                "support_agent": "support_agent",
                "checkpoint_agent": "checkpoint_agent",
                "planner_agent": "planner_agent",
            }
        )

        builder.add_conditional_edges(
            "planner_agent",
            self._route_condition,
            {
                "product_agent": "product_agent",
                "billing_agent": "billing_agent",
                "fallback_agent": "fallback_agent",
                "promotion_agent": "promotion_agent",
                "recommendation_agent": "recommendation_agent",
                "support_agent": "support_agent",
                "checkpoint_agent": "checkpoint_agent",
                "finops_agent": "finops_agent",
            }
        )

        builder.add_conditional_edges(
            "checkpoint_agent",
            self._checkpoint_post_condition,
            {
                "support_agent": "support_agent",
                END: END,
            }
        )

        # 3. 跨 Agent 协同边 (State Handoff)
        # BillingAgent 结束后，动态判断是否需要继续传递给 FinOpsAgent
        builder.add_conditional_edges(
            "billing_agent",
            self._billing_post_condition,
            {
                "finops_agent": "finops_agent",
                END: END
            }
        )

        # 各个子 Agent 执行完毕后，流程结束
        builder.add_edge("product_agent", END)
        builder.add_edge("fallback_agent", END)
        builder.add_edge("promotion_agent", END)
        builder.add_edge("recommendation_agent", END)
        builder.add_conditional_edges(
            "finops_agent",
            self._finops_post_condition,
            {
                "collaboration_agent": "collaboration_agent",
                END: END,
            },
        )
        builder.add_edge("collaboration_agent", END)
        builder.add_edge("support_agent", END)

        return builder.compile()

async def test_graph():
    manager = AgentGraphManager()
    graph = manager.build_graph()

    print("🚀 正在启动云平台智能客服系统 (Multi-Agent 编排模式)...")
    print("="*60)
    
    # 模拟第一轮对话
    state: AgentState = {
        "messages": [("user", "什么是VPC？")],
        "user_id": "user_1001",
        "tenant_id": "default_tenant",
        "session_id": "test_session_1",
        "memory_context": "",
        "next_agent": "",
        "metadata": {}
    }
    print(f"👤 用户: {state['messages'][0][1]}")
    
    result = await graph.ainvoke(state)
    print(f"🤖 AI: {result['messages'][-1].content}\n")

    # 模拟第二轮对话，测试路由
    state["messages"] = result["messages"]
    state["messages"].append(("user", "那帮我查一下我最近买了哪些机器？"))
    
    print(f"👤 用户: {state['messages'][-1][1]}")
    result = await graph.ainvoke(state)
    print(f"🤖 AI: {result['messages'][-1].content}\n")

if __name__ == "__main__":
    asyncio.run(test_graph())
