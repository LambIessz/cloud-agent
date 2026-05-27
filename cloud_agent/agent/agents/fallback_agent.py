from typing import Any, Dict

from langchain_core.messages import AIMessage

from core.workflow.state import AgentState
from core.workflow.request_context import ensure_request_metadata, get_request_id


class FallbackAgentNode:
    """
    Handles requests that are outside the cloud service assistant's scope.
    """

    async def __call__(self, state: AgentState) -> Dict[str, Any]:
        metadata = ensure_request_metadata(state.get("metadata", {}))
        metadata["handled_by"] = "fallback_agent"
        print(f"[FallbackAgent] request_id={get_request_id(metadata)} handled out-of-scope request")

        content = (
            "这个问题超出了当前云平台智能客服的处理范围。\n\n"
            "我目前主要支持这些云平台相关问题：\n"
            "1. 云产品咨询，例如 ECS、VPC、RDS、规格限制和操作说明。\n"
            "2. 账号本人名下的订单、账单和资源实例查询。\n"
            "3. 云产品选型推荐和配置建议。\n"
            "4. 云产品推广、返佣链接和推广物料生成。\n"
            "5. ECS、网络、安全组、RDS 连接等故障排查。\n"
            "6. 资源闲置、账单过高、降配建议等成本优化问题。\n\n"
            "你可以把问题改写成云平台场景，例如“我的 ECS 无法 SSH 连接，"
            "帮我排查安全组和公网 IP 配置”。"
        )

        return {"messages": [AIMessage(content=content)], "metadata": metadata}
