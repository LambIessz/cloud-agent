import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from core.mcp.mcp_manager import get_global_mcp_tool_registry
from core.workflow.state import AgentState
from core.workflow.llm_metrics import LLMCallMetricsCallback
from typing import Dict, Any
from agents.billing_agent import UserIdInjector, get_last_user_message_text
from tools.vector_tool import query_vector_db
from core.workflow.request_context import get_request_id
from core.workflow.context_manager import select_agent_memory_context

class RecommendationAgent:
    """
    智能推荐 Agent：负责根据用户的业务需求（类型、预算、并发等）进行云产品选型与推荐。
    它会调用向量数据库了解产品特性，并结合 MCP 获取真实可用的商品列表。
    """
    def __init__(self):
        dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env')
        load_dotenv(dotenv_path)

        self.llm = ChatOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            model=os.getenv("MODEL", "deepseek-chat"),
            base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
            temperature=0.3,
        )
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'mcp_servers.json')
        self.tool_registry = get_global_mcp_tool_registry(
            config_path,
            tool_interceptors=[UserIdInjector()],
        )

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        memory_context = select_agent_memory_context(state, "recommendation_agent")
        metadata = state.get("metadata", {})
        query = get_last_user_message_text(state)
        config = {
            "configurable": {
                "user_id": state.get("user_id", "unknown"),
                "tenant_id": state.get("tenant_id", "default_tenant"),
                "user_id_hash": metadata.get("user_id_hash", "unknown"),
                "request_id": get_request_id(metadata),
            },
            "callbacks": [
                LLMCallMetricsCallback(
                    request_id=get_request_id(metadata),
                    user_id_hash=metadata.get("user_id_hash", "unknown"),
                    tenant_id=state.get("tenant_id", "default_tenant"),
                    component="recommendation_agent",
                    operation="react_agent",
                    fallback_model=getattr(self.llm, "model_name", None)
                    or getattr(self.llm, "model", None),
                )
            ],
        }
        
        mcp_tools = await self.tool_registry.get_tools_for_agent(
            "recommendation",
            request_id=get_request_id(metadata),
            user_id_hash=metadata.get("user_id_hash", "unknown"),
            query=query,
        )
        
        # 组合向量工具与 MCP 工具
        tools = [query_vector_db] + mcp_tools

        system_prompt = f"""你是一个资深的云架构师和【智能推荐Agent】。
你的任务是根据用户的业务场景（如：Java+MySQL、高并发、特定预算），推荐最合适的云产品型号。

【工作流程】
1. 分析用户的业务需求（业务类型、日活/并发、预算、地域等）。如果用户只是单纯询问“有哪些产品”，请跳过分析，直接展示当前平台的商品库。
2. (必须) 调用 `get_promotable_products` 或 `search_product_catalog` 获取当前平台可供推荐和购买的真实商品列表。
3. 如果是选型推荐，调用 `query_vector_db` 检索相关规格（如 c7, g8a）的技术特性和适用场景。
4. 为用户精选 1-3 款最合适的商品，并给出专业的推荐理由（为什么选这款，满足了用户的什么痛点）。如果是询问列表，直接结构化列出。
5. (非常重要) 在推荐结论中，针对你推荐的商品，调用 `get_promotion_materials` 获取购买/活动链接，并在最终回复中附上这些直接购买链接。

【回答要求】
- 语气要像专业且热情的云架构师顾问。
- 必须包含具体的实例型号或产品名称。
- 必须条理清晰（使用列表、加粗）。
- 绝不要推荐 `get_promotable_products` 列表中不存在的虚构商品。
- 每次回答结尾，只需列出实际获取到数据的来源，格式如下：
  答案来源：
  - 向量检索：xxx.md
  （不要输出“未使用”的工具或“可信度”）

【系统提供的用户记忆/背景上下文】:
{memory_context if memory_context else "暂无背景上下文。"}
"""
        inner_agent = create_react_agent(
            model=self.llm,
            tools=tools,
            prompt=system_prompt
        )
        
        print("🔍 [RecommendationAgent] 正在进行智能产品选型与推荐...")
        
        result = await inner_agent.ainvoke(
            {"messages": state["messages"]},
            config=config
        )
        final_message = result["messages"][-1]
        return {"messages": [final_message]}
