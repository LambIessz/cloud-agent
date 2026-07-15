import os
from typing import Dict, Any
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from core.workflow.state import AgentState
from core.workflow.event_log import build_event, elapsed_ms, emit_event, now_ms
from core.workflow.request_context import ensure_request_metadata, get_request_id

class OrchestratorAgent:
    """
    中心路由节点 (Orchestrator/Router)
    负责分析用户意图，并将请求分发给相应的专门 Agent。
    """
    def __init__(self):
        dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        load_dotenv(dotenv_path)

        # 路由节点不需要复杂的工具，只需一个基础大模型来做分类决策
        self.llm = ChatOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            model=os.getenv("MODEL", "deepseek-chat"),
            base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
            temperature=0.1,
        )

    def _get_last_user_message(self, state: AgentState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return ""

        last_msg_obj = messages[-1]
        if isinstance(last_msg_obj, tuple):
            return str(last_msg_obj[1])
        if hasattr(last_msg_obj, "content"):
            return str(last_msg_obj.content)
        return str(last_msg_obj)

    def _has_any(self, text: str, keywords: list[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _first_non_negative_int(self, *values: Any) -> int | None:
        for value in values:
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return value
            if isinstance(value, float) and value >= 0 and value.is_integer():
                return int(value)
        return None

    def _mapping_or_empty(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _extract_llm_usage(self, response: Any) -> Dict[str, Any]:
        usage_metadata = self._mapping_or_empty(getattr(response, "usage_metadata", None))
        response_metadata = self._mapping_or_empty(getattr(response, "response_metadata", None))
        token_usage = self._mapping_or_empty(
            response_metadata.get("token_usage") or response_metadata.get("usage")
        )

        prompt_tokens = self._first_non_negative_int(
            usage_metadata.get("input_tokens"),
            usage_metadata.get("prompt_tokens"),
            token_usage.get("prompt_tokens"),
            token_usage.get("input_tokens"),
        )
        completion_tokens = self._first_non_negative_int(
            usage_metadata.get("output_tokens"),
            usage_metadata.get("completion_tokens"),
            token_usage.get("completion_tokens"),
            token_usage.get("output_tokens"),
        )
        model = (
            response_metadata.get("model_name")
            or response_metadata.get("model")
            or getattr(self.llm, "model_name", None)
            or getattr(self.llm, "model", None)
        )

        return {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    def _build_route_result(
        self,
        next_agent: str,
        metadata: Dict[str, Any],
        route_reason: str,
        primary_intent: str,
        secondary_intent: str | None = None,
        is_finops_workflow: bool | None = None,
    ) -> Dict[str, Any]:
        updated_metadata = ensure_request_metadata(metadata)
        updated_metadata["primary_intent"] = primary_intent
        updated_metadata["route_reason"] = route_reason
        if secondary_intent:
            updated_metadata["secondary_intent"] = secondary_intent
        else:
            updated_metadata.pop("secondary_intent", None)
        if is_finops_workflow is not None:
            updated_metadata["is_finops_workflow"] = is_finops_workflow

        request_id = get_request_id(updated_metadata)
        print(f"[Orchestrator] request_id={request_id} {route_reason}, route_to={next_agent}")
        emit_event(
            build_event(
                event_type="route_decision",
                request_id=request_id,
                user_id_hash=str(updated_metadata.get("user_id_hash", "unknown")),
                tenant_id=updated_metadata.get("tenant_id"),
                component="orchestrator",
                operation="route",
                route_to=next_agent,
                primary_intent=primary_intent,
                secondary_intent=secondary_intent,
                route_reason=route_reason,
                is_finops_workflow=updated_metadata.get("is_finops_workflow"),
            )
        )
        return {"next_agent": next_agent, "metadata": updated_metadata}

    def _rule_based_route(self, message: str, metadata: Dict[str, Any]) -> Dict[str, Any] | None:
        text = message.strip().lower()
        if not text:
            return self._build_route_result(
                "fallback_agent",
                metadata,
                "用户输入为空，进入能力边界兜底",
                "fallback",
                is_finops_workflow=False,
            )

        cloud_keywords = [
            "云", "ecs", "rds", "vpc", "eip", "实例", "服务器", "安全组", "公网",
            "内网", "专有网络", "数据库", "账单", "订单", "规格", "带宽", "网卡",
            "云盘", "公网ip", "公网 ip", "退款", "退订", "包年包月", "按量付费",
            "milvus", "redis", "mcp",
        ]
        out_of_scope_keywords = [
            "天气", "写诗", "写一首诗", "诗", "餐厅", "饭店", "电影", "股票",
            "旅游", "机票", "酒店", "星座", "笑话",
        ]
        cloud_business_keywords = [
            "咨询", "介绍", "说明", "查询", "查", "账单", "订单", "实例", "推荐",
            "选型", "推广", "返佣", "故障", "排查", "连接", "端口", "成本", "降本",
            "优化", "规格", "限制", "上限", "配额",
        ]
        if self._has_any(text, out_of_scope_keywords) and not self._has_any(text, cloud_keywords):
            return self._build_route_result(
                "fallback_agent",
                metadata,
                "识别到非云平台问题，进入 fallback",
                "fallback",
                is_finops_workflow=False,
            )

        support_keywords = [
            "ssh", "22端口", "22 端口", "端口不通", "连不上", "连接不上", "无法连接",
            "访问不了", "无法访问", "ping不通", "ping 不通", "安全组不通", "公网ip",
            "公网 ip", "rds连接失败", "rds 连接失败", "启动失败", "starting",
            "状态异常", "故障", "排查", "丢包", "延迟高",
        ]
        metric_problem_keywords = ["异常升高", "飙高", "负载过高", "高负载"]
        if self._has_any(text, support_keywords) or (
            self._has_any(text, metric_problem_keywords) and self._has_any(text, cloud_keywords)
        ):
            return self._build_route_result(
                "support_agent",
                metadata,
                "识别到云资源故障排查意图",
                "support",
                is_finops_workflow=False,
            )

        finops_keywords = [
            "账单太高", "费用太高", "太贵", "降本", "省钱", "成本优化", "优化成本",
            "降配", "闲置", "资源浪费", "成本太高",
        ]
        recommendation_keywords = [
            "推荐", "选型", "买哪款", "哪款合适", "配置建议", "适合", "预算",
            "高并发",
        ]
        recommendation_selection_keywords = ["怎么选", "如何选"]
        recommendation_hardware_keywords = ["gpu", "cpu", "实例", "规格", "配置"]
        if self._has_any(text, finops_keywords):
            secondary = "recommendation" if self._has_any(text, recommendation_keywords) else None
            return self._build_route_result(
                "billing_agent",
                metadata,
                "识别到成本优化意图，先进入 Billing 获取实例数据",
                "finops",
                secondary_intent=secondary,
                is_finops_workflow=True,
            )

        billing_keywords = [
            "我的订单", "订单记录", "查订单", "我的账单", "账单明细", "查账单",
            "我的实例", "我的服务器", "我买了哪些", "买了哪些", "购买了哪些",
            "名下资源", "资源实例",
        ]
        if self._has_any(text, billing_keywords):
            return self._build_route_result(
                "billing_agent",
                metadata,
                "识别到个人账单或资源查询意图",
                "billing",
                is_finops_workflow=False,
            )

        promotion_keywords = [
            "推广", "返佣", "佣金", "分享产品", "活动链接", "推广链接", "海报",
            "物料", "赚钱",
        ]
        if self._has_any(text, promotion_keywords):
            return self._build_route_result(
                "promotion_agent",
                metadata,
                "识别到营销推广意图",
                "promotion",
                is_finops_workflow=False,
            )

        if self._has_any(text, recommendation_keywords) or (
            self._has_any(text, recommendation_selection_keywords)
            and self._has_any(text, recommendation_hardware_keywords)
        ):
            return self._build_route_result(
                "recommendation_agent",
                metadata,
                "识别到云产品选型推荐意图",
                "recommendation",
                is_finops_workflow=False,
            )

        product_keywords = [
            "什么是", "介绍", "说明", "怎么用", "如何", "限制", "规格", "区别",
            "文档", "退款", "上限", "配额", "多少", "能挂载", "支持几个",
            "支持多少", "有哪些", "可用区", "地域", "vpc", "ecs", "rds", "安全组",
            "云盘", "网卡", "带宽",
        ]
        if self._has_any(text, product_keywords) and self._has_any(text, cloud_keywords):
            return self._build_route_result(
                "product_agent",
                metadata,
                "识别到云产品咨询意图",
                "product",
                is_finops_workflow=False,
            )

        if not self._has_any(text, cloud_keywords):
            return self._build_route_result(
                "fallback_agent",
                metadata,
                "未识别到云平台业务关键词，进入 fallback",
                "fallback",
                is_finops_workflow=False,
            )

        if self._has_any(text, ["云服务器", "云资源", "云主机"]):
            return self._build_route_result(
                "product_agent",
                metadata,
                "云资源咨询意图不明确，默认进入产品咨询",
                "product",
                is_finops_workflow=False,
            )

        return None

    async def route(self, state: AgentState) -> Dict[str, Any]:
        """
        根据用户的最新输入，决定路由走向。
        """
        last_message = self._get_last_user_message(state)
        memory_context = state.get("memory_context", "")
        metadata = ensure_request_metadata(state.get("metadata", {}))

        rule_result = self._rule_based_route(last_message, metadata)
        if rule_result:
            return rule_result

        system_prompt = f"""你是一个智能客服系统的总路由（Orchestrator）。
你的任务是根据用户的提问，决定将问题分发给哪个专业的 Agent 处理。

当前可用的子 Agent 有：
1. "product_agent" : 负责云产品介绍、资源规格说明、概念解释、操作指南等（非个人资产查询）。
2. "billing_agent" : 负责查询用户个人的云资源实例状态、购买的机器、订单记录、账单明细等。
3. "promotion_agent" : 负责处理想要分享产品、推广返佣、获取产品活动链接、获取海报等营销类需求。
4. "recommendation_agent" : 负责根据用户的业务需求（如Java+MySQL、高并发、特定预算、选型推荐）提供专业的云产品选型与推荐，包含具体的实例型号和配置建议。
5. "finops_agent_trigger" : 当用户表达“账单太贵”、“需要降本增效”、“资源闲置”、“帮我优化一下成本/服务器”等意图时选择此项。
6. "support_agent" : 负责 ECS 无法 SSH、安全组端口不通、公网 IP 无法访问、RDS 连接失败、实例状态异常、CPU/内存异常升高等故障排查。
7. "fallback_agent" : 负责非云平台问题、系统能力范围外的问题和无法判断的问题。

路由细则（高优先级）：
- 同时出现“推荐/选型”和“账单高/降本/优化成本”，必须优先输出 finops_agent_trigger。
- 用户问“某业务场景该选哪个实例/规格是否够用/推荐具体型号”（如 Java + MySQL，8核16G够不够）时，必须路由到 recommendation_agent。
- 用户问 ECS/RDS/VPC 的故障、连接失败、端口不通、状态异常时，必须路由到 support_agent。
- 只有在用户明确要求“深度调研报告/长篇架构对比/竞品调研文档/详细评估报告”时，才路由到 deep_research_agent。
- “推荐商品/推荐型号/选型建议/买哪款合适”默认属于 recommendation_agent，不要归给 product_agent。
- 非云平台问题必须输出 fallback_agent，不要归给 product_agent。

【背景记忆】：
{memory_context}

请仅输出你要路由到的名称（必须是: product_agent, billing_agent, promotion_agent, recommendation_agent, finops_agent_trigger, support_agent, fallback_agent 中的一个），不要输出任何其他解释性文字。
如果你无法判断，默认输出 fallback_agent。
"""

        llm_start_ms = now_ms()
        request_id = get_request_id(metadata)
        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=last_message)
            ])
        except Exception as exc:
            emit_event(
                build_event(
                    event_type="llm_call",
                    request_id=request_id,
                    user_id_hash=str(metadata.get("user_id_hash", "unknown")),
                    tenant_id=metadata.get("tenant_id"),
                    component="orchestrator",
                    operation="route_classification",
                    status="error",
                    latency_ms=elapsed_ms(llm_start_ms),
                    error_type=exc.__class__.__name__,
                )
            )
            raise
        emit_event(
            build_event(
                event_type="llm_call",
                request_id=request_id,
                user_id_hash=str(metadata.get("user_id_hash", "unknown")),
                tenant_id=metadata.get("tenant_id"),
                component="orchestrator",
                operation="route_classification",
                status="success",
                latency_ms=elapsed_ms(llm_start_ms),
                **self._extract_llm_usage(response),
            )
        )
        
        decision = response.content.strip().lower()
        print(f"[Orchestrator] DeepSeek raw decision: '{decision}'")
        if "finops" in decision:
            return self._build_route_result(
                "billing_agent",
                metadata,
                "LLM 识别到成本优化意图，先进入 Billing 获取实例数据",
                "finops",
                is_finops_workflow=True,
            )
        elif "billing" in decision:
            return self._build_route_result(
                "billing_agent",
                metadata,
                "LLM 识别到常规账单查询意图",
                "billing",
                is_finops_workflow=False,
            )
        elif "promotion" in decision:
            return self._build_route_result(
                "promotion_agent",
                metadata,
                "LLM 识别到营销推广意图",
                "promotion",
                is_finops_workflow=False,
            )
        elif "recommendation" in decision:
            return self._build_route_result(
                "recommendation_agent",
                metadata,
                "LLM 识别到选型推荐意图",
                "recommendation",
                is_finops_workflow=False,
            )
        elif "support" in decision:
            return self._build_route_result(
                "support_agent",
                metadata,
                "LLM 识别到故障排查意图",
                "support",
                is_finops_workflow=False,
            )
        elif "product" in decision:
            return self._build_route_result(
                "product_agent",
                metadata,
                "LLM 识别到产品咨询意图",
                "product",
                is_finops_workflow=False,
            )
        else:
            return self._build_route_result(
                "fallback_agent",
                metadata,
                "LLM 未能给出可信路由，进入 fallback",
                "fallback",
                is_finops_workflow=False,
            )
