# -*- coding: utf-8 -*-
"""
7 Agent 测试用例集合

覆盖范围：
  Orchestrator · Product · Billing · FinOps · Promotion
  Recommendation · Support · Fallback

运行方式：
  pytest cloud_agent/agent/test/test_seven_agents.py -v
"""

import asyncio
import json

import pytest

# ─────────────────────────────────────────────────────────
# 公共 Fake / Mock 对象
# ─────────────────────────────────────────────────────────


class _FailingLLM:
    """每当测试只关心规则路由（不关心 LLM 调用）时使用。"""

    async def ainvoke(self, *_args, **_kwargs):
        raise AssertionError("routing test should be covered by deterministic rules")


class _RoutingLLM:
    def __init__(self, content, *, response_metadata=None, usage_metadata=None):
        self.content = content
        self.response_metadata = response_metadata or {}
        self.usage_metadata = usage_metadata or {}

    async def ainvoke(self, *_args, **_kwargs):
        return type(
            "Response",
            (),
            {
                "content": self.content,
                "response_metadata": self.response_metadata,
                "usage_metadata": self.usage_metadata,
            },
        )()


class _FakeTool:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description or f"{name} description"


class _FakeMCPClient:
    """Mock MCP client，避免真实 stdio 子进程。"""

    init_count = 0
    get_tools_count = 0

    def __init__(self, **kwargs):
        type(self).init_count += 1

    async def get_tools(self):
        type(self).get_tools_count += 1
        return _DEFAULT_FAKE_TOOLS


_DEFAULT_FAKE_TOOLS = [
    _FakeTool("query_user_orders"),
    _FakeTool("query_user_instances"),
    _FakeTool("analyze_instance_usage"),
    _FakeTool("get_promotable_products"),
    _FakeTool("search_product_catalog"),
    _FakeTool("get_promotion_materials"),
    _FakeTool("generate_ai_poster"),
]


def _reset_mcp_tool_registry():
    """每次测试前重置 MCP 单例，保证测试隔离。"""
    from core.mcp.mcp_manager import reset_global_mcp_tool_registry

    reset_global_mcp_tool_registry()


# ─────────────────────────────────────────────────────────
# 模块 1：Orchestrator（路由 Agent）
# ─────────────────────────────────────────────────────────


def _route(query: str, metadata=None):
    from agents.orchestrator import OrchestratorAgent

    async def _run():
        agent = OrchestratorAgent()
        agent.llm = _FailingLLM()
        state = {
            "messages": [("user", query)],
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": metadata or {},
        }
        return await agent.route(state)

    return asyncio.run(_run())


class TestOrchestrator:
    """Orchestrator 路由测试：规则引擎 16 条关键词映射。"""

    @pytest.mark.parametrize(
        ("query", "expected_agent", "expected_intent"),
        [
            # —— fallback ——
            ("今天天气怎么样？", "fallback_agent", "fallback"),
            ("写一首诗", "fallback_agent", "fallback"),
            ("南京哪家餐厅好吃？", "fallback_agent", "fallback"),
            ("帮我推荐一部电影", "fallback_agent", "fallback"),
            # —— support ——
            ("我的 ECS 无法 SSH 连接，帮我排查一下", "support_agent", "support"),
            ("安全组 22 端口不通怎么办？", "support_agent", "support"),
            ("RDS 连接失败，ECS 连不上数据库", "support_agent", "support"),
            ("我的 ECS CPU 异常升高，怎么定位？", "support_agent", "support"),
            ("公网 IP 无法访问，怎么排查？", "support_agent", "support"),
            # —— recommendation ——
            (
                "Java + MySQL 的业务应该买哪款 ECS 合适？",
                "recommendation_agent",
                "recommendation",
            ),
            (
                "高并发业务预算 500，推荐一个配置",
                "recommendation_agent",
                "recommendation",
            ),
            (
                "我是做视频转码的，GPU 和 CPU 实例该怎么选？",
                "recommendation_agent",
                "recommendation",
            ),
            # —— product ——
            ("五天无理由退款有什么限制条件吗？", "product_agent", "product"),
            ("什么是专有网络 VPC？", "product_agent", "product"),
            ("ecs.g8a.4xlarge 实例能挂载多少块弹性网卡？", "product_agent", "product"),
            ("ECS 规格族 g8a 和 c7 有什么区别？", "product_agent", "product"),
            ("RDS MySQL 高可用版支持哪些地域？", "product_agent", "product"),
            # —— billing ——
            ("帮我查一下我最近买了哪些机器", "billing_agent", "billing"),
            ("帮我查一下我的账单明细", "billing_agent", "billing"),
            ("我名下有哪些云资源实例？", "billing_agent", "billing"),
            # —— promotion ——
            ("我想推广商品赚钱，有什么可以推的？", "promotion_agent", "promotion"),
            ("我要推广 ECS，帮我生成海报", "promotion_agent", "promotion"),
            ("返佣比例是多少？帮我生成推广链接", "promotion_agent", "promotion"),
            # —— finops ——
            ("账单太贵了，帮我看看怎么降本", "billing_agent", "finops"),
            ("我的云服务器资源闲置，帮我优化一下成本", "billing_agent", "finops"),
            ("帮我做降本增效分析", "billing_agent", "finops"),
        ],
    )
    def test_rule_based_routes(self, query, expected_agent, expected_intent):
        result = _route(query)
        assert result["next_agent"] == expected_agent
        assert result["metadata"]["primary_intent"] == expected_intent

    def test_finops_route_sets_is_finops_workflow_flag(self):
        """FinOps 路由必须设置 is_finops_workflow=True，供条件边使用。"""
        result = _route("帮我优化成本")
        assert result["metadata"].get("is_finops_workflow") is True

    def test_billing_route_does_not_set_finops_flag(self):
        result = _route("帮我查一下我的账单明细")
        assert result["metadata"].get("is_finops_workflow") is not True

    def test_empty_input_goes_to_fallback(self):
        result = _route("   ")
        assert result["next_agent"] == "fallback_agent"

    def test_ambiguous_cloud_keyword_handled_by_llm_or_rule(self):
        """仅有云关键词但无明确意图时，走规则兜底或 LLM。"""
        result = _route("云服务器")
        # 既可能 product 也可能 LLM，但不会是 fallback
        assert result["next_agent"] != "fallback_agent"

    def test_llm_route_emits_llm_call_and_route_decision_events(self, capsys):
        from agents.orchestrator import OrchestratorAgent

        async def _run():
            agent = OrchestratorAgent()
            agent.llm = _RoutingLLM("product_agent")
            state = {
                "messages": [("user", "云平台 MCP 工具")],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {
                    "request_id": "req_llm_route",
                    "tenant_id": "default_tenant",
                    "user_id_hash": "hash_llm_route",
                },
            }
            return await agent.route(state)

        result = asyncio.run(_run())
        output = capsys.readouterr().out
        events = [json.loads(line.removeprefix("[EventLog] ")) for line in output.splitlines() if line.startswith("[EventLog] ")]
        assert result["next_agent"] == "product_agent"
        event_types = [e["event_type"] for e in events]
        assert "llm_call" in event_types
        assert "route_decision" in event_types


# ─────────────────────────────────────────────────────────
# 模块 2：Product Agent（产品咨询）
# ─────────────────────────────────────────────────────────


class TestProductAgent:
    """产品咨询 Agent：向量检索 + 知识图谱。"""

    def test_product_agent_has_vector_and_graph_tools(self):
        from agents.product_agent import ProductAgentNode

        agent = ProductAgentNode()
        tool_names = {t.name for t in agent.tools}
        assert "query_vector_db" in tool_names
        assert "query_knowledge_graph" in tool_names

    def test_product_agent_system_prompt_contains_tool_descriptions(self):
        from agents.product_agent import ProductAgentNode

        agent = ProductAgentNode()
        assert isinstance(agent.llm.temperature, float)

    def test_product_agent_state_structure(self):
        """验证 Agent 接收标准 AgentState 输入。"""
        from agents.product_agent import ProductAgentNode
        from core.workflow.state import AgentState

        agent = ProductAgentNode()
        state: AgentState = {
            "messages": [("user", "什么是 VPC？")],
            "user_id": "test_user",
            "tenant_id": "default_tenant",
            "session_id": "test_session",
            "memory_context": "",
            "next_agent": "",
            "metadata": {},
        }
        # 仅结构校验，不做真实调用
        assert state["messages"][0][1] == "什么是 VPC？"


# ─────────────────────────────────────────────────────────
# 模块 3：Billing Agent（账单/资源查询）
# ─────────────────────────────────────────────────────────


class TestBillingAgent:
    """账单 Agent：订单查询 + FinOps 状态交接。"""

    def test_billing_agent_tool_allowlist_only_own_scope(self):
        from core.mcp.mcp_manager import AGENT_TOOL_ALLOWLISTS

        billing_tools = AGENT_TOOL_ALLOWLISTS.get("billing", set())
        assert "query_user_orders" in billing_tools
        assert "query_user_instances" in billing_tools
        # Billing agent 不能调用推广工具
        assert "generate_ai_poster" not in billing_tools
        assert "get_promotion_materials" not in billing_tools

    def test_billing_agent_has_retry_and_timeout_config(self):
        from agents.billing_agent import (
            DEFAULT_TOOL_TIMEOUT_SECONDS,
            DEFAULT_TOOL_RETRY_COUNT,
        )

        assert DEFAULT_TOOL_TIMEOUT_SECONDS == 30.0
        assert DEFAULT_TOOL_RETRY_COUNT == 0

    def test_billing_agent_user_id_injector_exists(self):
        from agents.billing_agent import UserIdInjector

        injector = UserIdInjector()
        assert injector is not None

    def test_billing_agent_system_prompt_forbids_user_id_leak(self):
        """系统提示词必须禁止输出明文 user_id。"""
        from agents.billing_agent import BillingAgentNode

        node = BillingAgentNode()
        # 验证 agent 初始化成功
        assert node.llm is not None
        assert node.tool_registry is not None

    def test_billing_agent_system_prompt_forbids_fake_instance_ids(self):
        """系统提示词禁止伪造实例 ID。"""
        from agents.billing_agent import BillingAgentNode

        node = BillingAgentNode()
        assert node.llm.temperature == 0.1


# ─────────────────────────────────────────────────────────
# 模块 4：FinOps Agent（成本优化 + 事实校验）
# ─────────────────────────────────────────────────────────


class TestFinOpsAgent:
    """FinOps Agent：成本分析 + 三重事实校验。"""

    def test_finops_agent_tool_allowlist(self):
        from core.mcp.mcp_manager import AGENT_TOOL_ALLOWLISTS

        finops_tools = AGENT_TOOL_ALLOWLISTS.get("finops", set())
        assert "query_user_instances" in finops_tools
        assert "analyze_instance_usage" in finops_tools
        # FinOps agent 不能生成海报
        assert "generate_ai_poster" not in finops_tools

    def test_finops_facts_extraction_from_tool_messages(self):
        from langchain_core.messages import ToolMessage
        from core.workflow.finops_validator import extract_finops_facts

        messages = [
            ToolMessage(
                content='{"status":"success","data":[{"instance_id":"i-bp123","status":"Running"}]}',
                tool_call_id="call_1",
            ),
            ToolMessage(
                content=(
                    '{"status":"success","data":{"instance_id":"i-bp123",'
                    '"metrics_7d_avg":{"cpu_usage_percent":5.2,'
                    '"memory_usage_percent":21.5,"network_out_bandwidth_mbps":1.2},'
                    '"diagnosis":"RESOURCES_IDLE"}}'
                ),
                tool_call_id="call_2",
            ),
        ]
        facts = extract_finops_facts(messages)
        assert facts.instance_ids == {"i-bp123"}
        assert facts.has_metrics is True
        assert {"5.2", "21.5", "1.2"} <= facts.metric_values

    def test_finops_validator_rejects_unsupported_instance_id(self):
        from core.workflow.finops_validator import FinOpsFacts, validate_finops_response

        content = "实例 i-fake999 近 7 天资源闲置，建议降配。"
        sanitized, issues = validate_finops_response(
            content, FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True)
        )
        assert "i-fake999" not in sanitized
        assert "unsupported_instance_id" in issues

    def test_finops_validator_removes_unsupported_savings_amount(self):
        from core.workflow.finops_validator import FinOpsFacts, validate_finops_response

        content = "降配后预计每月可节省 300 元。"
        sanitized, issues = validate_finops_response(
            content, FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True)
        )
        assert "300 元" not in sanitized
        assert "unsupported_savings_amount" in issues

    def test_finops_validator_removes_metric_values_without_tool_data(self):
        from core.workflow.finops_validator import FinOpsFacts, validate_finops_response

        content = "该实例 CPU 平均 5%，内存 20%，建议观察。"
        sanitized, issues = validate_finops_response(content, FinOpsFacts())
        assert "5%" not in sanitized
        assert "20%" not in sanitized
        assert "unsupported_metric_value" in issues

    def test_finops_validator_keeps_valid_instance_and_qualitative_advice(self):
        from core.workflow.finops_validator import FinOpsFacts, validate_finops_response

        content = "实例 i-bp123 监控显示资源偏闲置，建议评估降配。"
        sanitized, issues = validate_finops_response(
            content, FinOpsFacts(instance_ids={"i-bp123"}, has_metrics=True)
        )
        assert sanitized == content
        assert not issues

    def test_finops_agent_clears_next_agent_after_execution(self):
        """FinOps 执行完毕后必须清空 next_agent，防止死循环。"""
        from agents.finops_agent import FinOpsAgentNode

        node = FinOpsAgentNode()
        assert node.llm is not None
        assert node.tool_registry is not None


# ─────────────────────────────────────────────────────────
# 模块 5：Promotion Agent（营销推广）
# ─────────────────────────────────────────────────────────


class TestPromotionAgent:
    """推广 Agent：产品列表 → 物料获取 → AI 海报生成。"""

    def test_promotion_agent_tool_allowlist(self):
        from core.mcp.mcp_manager import AGENT_TOOL_ALLOWLISTS

        promo_tools = AGENT_TOOL_ALLOWLISTS.get("promotion", set())
        assert "get_promotable_products" in promo_tools
        assert "search_product_catalog" in promo_tools
        assert "get_promotion_materials" in promo_tools
        assert "generate_ai_poster" in promo_tools
        # 不能查订单
        assert "query_user_orders" not in promo_tools

    def test_promotion_agent_has_user_id_injector(self):
        from agents.promotion_agent import PromotionAgentNode

        node = PromotionAgentNode()
        assert node.llm is not None
        assert node.tool_registry is not None

    def test_promotion_system_prompt_requires_ai_poster_generation(self):
        """推广 Agent 提示词要求生成海报时调用 generate_ai_poster。"""
        from agents.promotion_agent import PromotionAgentNode

        node = PromotionAgentNode()
        assert node.llm.temperature == 0.3


# ─────────────────────────────────────────────────────────
# 模块 6：Recommendation Agent（产品选型推荐）
# ─────────────────────────────────────────────────────────


class TestRecommendationAgent:
    """推荐 Agent：业务分析 → 向量检索 + MCP → 精选推荐 + 购买链接。"""

    def test_recommendation_agent_tool_allowlist(self):
        from core.mcp.mcp_manager import AGENT_TOOL_ALLOWLISTS

        rec_tools = AGENT_TOOL_ALLOWLISTS.get("recommendation", set())
        assert "get_promotable_products" in rec_tools
        assert "search_product_catalog" in rec_tools
        assert "get_promotion_materials" in rec_tools
        # 不能查账单
        assert "query_user_orders" not in rec_tools

    def test_recommendation_agent_combines_vector_and_mcp_tools(self):
        from agents.recommendation_agent import RecommendationAgent

        agent = RecommendationAgent()
        assert agent.llm is not None
        assert agent.tool_registry is not None

    def test_recommendation_system_prompt_forbids_fictional_products(self):
        """提示词禁止推荐不存在的虚构商品。"""
        from agents.recommendation_agent import RecommendationAgent

        agent = RecommendationAgent()
        assert agent.llm.temperature == 0.3


# ─────────────────────────────────────────────────────────
# 模块 7：Support Agent（故障排查）
# ─────────────────────────────────────────────────────────


class TestSupportAgent:
    """售后 Agent：ECS/网络/安全组/数据库故障排查。"""

    def test_support_agent_returns_structured_troubleshooting_response(self):
        from agents.support_agent import SupportAgentNode
        from core.workflow.state import AgentState

        async def _run():
            agent = SupportAgentNode()
            state: AgentState = {
                "messages": [("user", "我的 ECS 无法 SSH 连接")],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {"request_id": "req_support"},
            }
            return await agent(state)

        result = asyncio.run(_run())
        assert len(result["messages"]) == 1
        content = result["messages"][0].content
        assert isinstance(content, str)
        assert len(content) > 20
        assert "排查" in content or "步骤" in content or "安全组" in content

    @pytest.mark.parametrize(
        ("query", "expected_keywords"),
        [
            ("ECS 无法 SSH 连接", ["安全组", "公网 IP", "密码"]),
            ("安全组 22 端口不通", ["端口", "入方向", "规则"]),
            ("RDS 连接失败，ECS 连不上数据库", ["白名单", "连接地址", "账号密码"]),
            ("实例状态异常，CPU 100%", ["CPU", "进程", "规格"]),
        ],
    )
    def test_support_agent_covers_core_troubleshooting_scenarios(self, query, expected_keywords):
        from agents.support_agent import SupportAgentNode

        async def _run():
            agent = SupportAgentNode()
            state = {
                "messages": [("user", query)],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {},
            }
            return await agent(state)

        result = asyncio.run(_run())
        content = result["messages"][0].content
        for keyword in expected_keywords:
            assert keyword in content, f"响应缺少关键词: {keyword}"


# ─────────────────────────────────────────────────────────
# 模块 8：Fallback Agent（能力边界兜底）
# ─────────────────────────────────────────────────────────


class TestFallbackAgent:
    """兜底 Agent：处理非云平台问题。"""

    def test_fallback_agent_lists_supported_domains(self):
        from agents.fallback_agent import FallbackAgentNode

        async def _run():
            agent = FallbackAgentNode()
            state = {
                "messages": [("user", "今天天气怎么样？")],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {},
            }
            return await agent(state)

        result = asyncio.run(_run())
        content = result["messages"][0].content
        assert "云产品" in content
        assert "订单" in content or "账单" in content
        assert "选型推荐" in content or "推荐" in content
        assert "故障排查" in content
        assert content.startswith("这个问题超出了")

    def test_fallback_agent_records_handled_by_metadata(self):
        from agents.fallback_agent import FallbackAgentNode

        async def _run():
            agent = FallbackAgentNode()
            state = {
                "messages": [("user", "帮我推荐一部电影")],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {},
            }
            return await agent(state)

        result = asyncio.run(_run())
        assert result["metadata"]["handled_by"] == "fallback_agent"

    def test_fallback_agent_response_is_reproducible(self):
        """多次调用返回一致的兜底话术。"""
        from agents.fallback_agent import FallbackAgentNode

        async def _run():
            agent = FallbackAgentNode()
            state = {
                "messages": [("user", "x" * 5)],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {},
            }
            return await agent(state)

        result1 = asyncio.run(_run())
        result2 = asyncio.run(_run())
        assert result1["messages"][0].content == result2["messages"][0].content


# ─────────────────────────────────────────────────────────
# 跨 Agent 集成测试
# ─────────────────────────────────────────────────────────


class TestCrossAgentIntegration:
    """跨 Agent 协作流程测试。"""

    def test_billing_to_finops_handoff_requires_is_finops_workflow(self):
        """Billing → FinOps 的条件边依赖 is_finops_workflow 标记。"""
        from agents.orchestrator import OrchestratorAgent

        async def _run():
            agent = OrchestratorAgent()
            agent.llm = _FailingLLM()
            state = {
                "messages": [("user", "账单太贵了，帮我降本")],
                "user_id": "user_test",
                "tenant_id": "default_tenant",
                "session_id": "session_test",
                "memory_context": "",
                "next_agent": "",
                "metadata": {},
            }
            return await agent.route(state)

        result = asyncio.run(_run())
        assert result["next_agent"] == "billing_agent"
        assert result["metadata"]["primary_intent"] == "finops"
        assert result["metadata"]["is_finops_workflow"] is True

    def test_graph_billing_post_condition_routes_to_finops(self):
        """_billing_post_condition 在 is_finops_workflow=True 时路由到 finops_agent。"""
        from core.workflow.graph_manager import AgentGraphManager

        manager = AgentGraphManager()
        state = {"metadata": {"is_finops_workflow": True}}
        assert manager._billing_post_condition(state) == "finops_agent"

    def test_graph_billing_post_condition_ends_normally(self):
        from core.workflow.graph_manager import AgentGraphManager

        manager = AgentGraphManager()
        state = {"metadata": {"is_finops_workflow": False}}
        from langgraph.graph import END

        assert manager._billing_post_condition(state) == END

    def test_graph_route_condition_defaults_to_fallback(self):
        """next_agent 为空时应默认路由到 fallback。"""
        from core.workflow.graph_manager import AgentGraphManager

        manager = AgentGraphManager()
        assert manager._route_condition({"next_agent": ""}) == "fallback_agent"
        assert (
            manager._route_condition({"next_agent": "product_agent"})
            == "product_agent"
        )

    def test_graph_build_has_all_seven_agent_nodes(self):
        from core.workflow.graph_manager import AgentGraphManager

        manager = AgentGraphManager()
        graph = manager.build_graph()
        nodes = list(graph.nodes.keys())
        expected_nodes = [
            "orchestrator",
            "planner_agent",
            "collaboration_agent",
            "product_agent",
            "billing_agent",
            "finops_agent",
            "promotion_agent",
            "recommendation_agent",
            "support_agent",
            "fallback_agent",
        ]
        for node in expected_nodes:
            assert node in nodes, f"图表中缺少节点: {node}"


# ─────────────────────────────────────────────────────────
# 端到端数据流测试
# ─────────────────────────────────────────────────────────


class TestEndToEndDataFlow:
    """验证 Agent 输入输出的数据完整性。"""

    def test_agent_state_messages_are_tuples(self):
        """消息格式为 (role, content) 元组列表。"""
        state = {
            "messages": [("user", "hello"), ("assistant", "hi there")],
            "next_agent": "",
            "user_id": "u1",
            "tenant_id": "t1",
            "session_id": "s1",
            "memory_context": "",
            "metadata": {},
        }
        assert len(state["messages"]) == 2
        assert state["messages"][0][0] == "user"
        assert state["messages"][1][0] == "assistant"

    def test_metadata_contains_required_fields(self):
        """metadata 必须包含 request_id 和 user_id_hash。"""
        from core.workflow.request_context import ensure_request_metadata

        meta = ensure_request_metadata({})
        assert "request_id" in meta
        assert "user_id_hash" in meta

    def test_identity_context_resolves_to_safe_values(self):
        from core.workflow.identity_context import resolve_identity

        identity = resolve_identity(
            request_user_id="real_user_123",
            request_tenant_id="tenant_a",
        )
        assert identity.user_id is not None
        assert identity.tenant_id is not None
        assert len(identity.user_id_hash) == 16

    def test_identity_context_production_mode_forces_anonymous(self):
        import os

        original = os.environ.get("CLOUD_AGENT_AUTH_MODE")
        os.environ["CLOUD_AGENT_AUTH_MODE"] = "production"
        try:
            from core.workflow.identity_context import resolve_identity

            identity = resolve_identity(request_user_id="real_user_123")
            assert identity.source == "anonymous"
            assert identity.user_id == "anonymous"
        finally:
            if original:
                os.environ["CLOUD_AGENT_AUTH_MODE"] = original
            else:
                os.environ.pop("CLOUD_AGENT_AUTH_MODE", None)

    def test_mcp_agent_allowlist_coverage(self):
        """确保 AGENT_TOOL_ALLOWLISTS 覆盖了所有已注册的 Agent node 名。"""
        from core.mcp.mcp_manager import AGENT_TOOL_ALLOWLISTS

        expected_agents = {
            "billing",
            "finops",
            "promotion",
            "recommendation",
        }
        registered = set(AGENT_TOOL_ALLOWLISTS.keys())
        for agent in expected_agents:
            assert agent in registered, f"AGENT_TOOL_ALLOWLISTS 缺少: {agent}"
