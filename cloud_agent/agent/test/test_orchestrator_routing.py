import asyncio
import json

import pytest

from agents.orchestrator import OrchestratorAgent
from core.workflow.metrics import render_prometheus_metrics, reset_metrics


class _FailingLLM:
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


def _route(query: str, metadata=None):
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


@pytest.mark.parametrize(
    ("query", "expected_agent", "expected_intent"),
    [
        ("今天天气怎么样？", "fallback_agent", "fallback"),
        ("帮我写一首关于云服务器的诗", "fallback_agent", "fallback"),
        ("我的 ECS 无法 SSH 连接，帮我排查一下", "support_agent", "support"),
        ("安全组 22 端口不通怎么办？", "support_agent", "support"),
        ("RDS 连接失败，ECS 连不上数据库", "support_agent", "support"),
        ("我的 ECS CPU 异常升高，怎么定位？", "support_agent", "support"),
        ("Java + MySQL 的业务应该买哪款 ECS 合适？", "recommendation_agent", "recommendation"),
        ("高并发业务预算 500，推荐一个配置", "recommendation_agent", "recommendation"),
        ("五天无理由退款有什么限制条件吗？", "product_agent", "product"),
        ("什么是专有网络 VPC？", "product_agent", "product"),
        ("ecs.g8a.4xlarge 实例能挂载多少块弹性网卡？", "product_agent", "product"),
        ("ecs.c7.large 的带宽上限是多少？", "product_agent", "product"),
        ("帮我查一下我最近买了哪些机器", "billing_agent", "billing"),
        ("帮我查一下我的账单明细", "billing_agent", "billing"),
        ("我想推广商品赚钱，有什么可以推的？", "promotion_agent", "promotion"),
        ("我要推广 ECS，帮我生成海报", "promotion_agent", "promotion"),
    ],
)
def test_rule_based_routes(query, expected_agent, expected_intent):
    result = _route(query)

    assert result["next_agent"] == expected_agent
    assert result["metadata"]["primary_intent"] == expected_intent


@pytest.mark.parametrize(
    "query",
    [
        "帮我推荐一个便宜的机器，我的账单太高了。",
        "帮我查我的实例并优化成本",
        "这些服务器是不是闲置了，帮我降本",
    ],
)
def test_cost_related_queries_prefer_finops_workflow(query):
    result = _route(query)

    assert result["next_agent"] == "billing_agent"
    assert result["metadata"]["primary_intent"] == "finops"
    assert result["metadata"]["is_finops_workflow"] is True
    collaboration_state = result["metadata"]["collaboration_state"]
    assert collaboration_state["mode"] == "billing_finops_synthesis"
    assert collaboration_state["participants"] == [
        "billing_agent",
        "finops_agent",
        "collaboration_agent",
    ]
    assert collaboration_state["status"] == "collecting"


def test_cost_and_recommendation_records_secondary_intent():
    result = _route("帮我推荐一个便宜的机器，我的账单太高了。")

    assert result["metadata"]["secondary_intent"] == "recommendation"


def test_regular_billing_query_does_not_trigger_finops():
    result = _route("帮我查一下我的账单明细")

    assert result["next_agent"] == "billing_agent"
    assert result["metadata"]["primary_intent"] == "billing"
    assert result["metadata"]["is_finops_workflow"] is False


def test_high_risk_action_routes_to_checkpoint_agent():
    result = _route("帮我重启 ECS i-bp123")

    assert result["next_agent"] == "checkpoint_agent"
    assert result["metadata"]["primary_intent"] == "checkpoint"
    assert result["metadata"]["checkpoint_resume_agent"] == "support_agent"
    assert result["metadata"]["checkpoint_reason"]


def test_route_preserves_existing_request_id():
    result = _route("今天天气怎么样？", metadata={"request_id": "req_test_123"})

    assert result["metadata"]["request_id"] == "req_test_123"


def test_route_generates_request_id_when_missing():
    result = _route("今天天气怎么样？")

    assert result["metadata"]["request_id"].startswith("req_")


def test_route_emits_structured_route_decision_without_plain_user_id(capsys):
    result = _route(
        "浠婂ぉ澶╂皵鎬庝箞鏍凤紵",
        metadata={
            "request_id": "req_route_event",
            "tenant_id": "tenant_a",
            "user_id_hash": "hash_route",
        },
    )

    output = capsys.readouterr().out
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))

    assert "user_test" not in output
    assert len(events) == 1
    assert events[0]["event_type"] == "route_decision"
    assert events[0]["request_id"] == "req_route_event"
    assert events[0]["user_id_hash"] == "hash_route"
    assert events[0]["tenant_id"] == "tenant_a"
    assert events[0]["component"] == "orchestrator"
    assert events[0]["operation"] == "route"
    assert events[0]["route_to"] == result["next_agent"] == "fallback_agent"
    assert events[0]["primary_intent"] == "fallback"
    assert events[0]["is_finops_workflow"] is False


def test_llm_route_emits_llm_call_event_without_prompt_or_plain_user_id(capsys):
    async def _run():
        agent = OrchestratorAgent()
        agent.llm = _RoutingLLM("product_agent")
        state = {
            "messages": [("user", "mcp")],
            "user_id": "user_test",
            "tenant_id": "tenant_a",
            "session_id": "session_test",
            "memory_context": "secret memory context",
            "next_agent": "",
            "metadata": {
                "request_id": "req_llm_route",
                "tenant_id": "tenant_a",
                "user_id_hash": "hash_llm_route",
            },
        }
        return await agent.route(state)

    result = asyncio.run(_run())
    output = capsys.readouterr().out
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))

    assert "user_test" not in output
    assert "secret memory context" not in output
    assert result["next_agent"] == "product_agent"
    assert [event["event_type"] for event in events] == ["llm_call", "route_decision"]
    assert events[0]["request_id"] == "req_llm_route"
    assert events[0]["user_id_hash"] == "hash_llm_route"
    assert events[0]["tenant_id"] == "tenant_a"
    assert events[0]["component"] == "orchestrator"
    assert events[0]["operation"] == "route_classification"
    assert events[0]["status"] == "success"
    assert isinstance(events[0]["latency_ms"], int)
    assert "prompt" not in events[0]
    assert "completion" not in events[0]
    assert "query" not in events[0]


def test_llm_route_records_usage_for_estimated_cost_metrics(monkeypatch, tmp_path, capsys):
    pricing_config = tmp_path / "llm_pricing.yml"
    pricing_config.write_text(
        "\n".join(
            [
                "llm_pricing:",
                "  deepseek-chat:",
                "    prompt_usd_per_1k: 0.5",
                "    completion_usd_per_1k: 1.5",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUD_AGENT_LLM_PRICING_CONFIG", str(pricing_config))
    reset_metrics()

    async def _run():
        agent = OrchestratorAgent()
        agent.llm = _RoutingLLM(
            "product_agent",
            response_metadata={
                "model_name": "deepseek-chat",
                "token_usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 2000,
                },
            },
        )
        state = {
            "messages": [("user", "mcp")],
            "user_id": "user_test",
            "tenant_id": "tenant_a",
            "session_id": "session_test",
            "memory_context": "secret memory context",
            "next_agent": "",
            "metadata": {
                "request_id": "req_llm_cost_route",
                "tenant_id": "tenant_a",
                "user_id_hash": "hash_llm_cost_route",
            },
        }
        return await agent.route(state)

    result = asyncio.run(_run())
    output = capsys.readouterr().out
    events = [
        json.loads(line.removeprefix("[EventLog] "))
        for line in output.splitlines()
        if line.startswith("[EventLog] ")
    ]
    metrics_text = render_prometheus_metrics()

    assert result["next_agent"] == "product_agent"
    assert events[0]["event_type"] == "llm_call"
    assert events[0]["model"] == "deepseek-chat"
    assert events[0]["prompt_tokens"] == 1000
    assert events[0]["completion_tokens"] == 2000
    assert "prompt" not in events[0]
    assert "completion" not in events[0]
    assert "query" not in events[0]
    assert (
        'cloud_agent_llm_prompt_token_total{component="orchestrator",'
        'model="deepseek-chat",operation="route_classification",status="success"} 1000'
    ) in metrics_text
    assert (
        'cloud_agent_llm_completion_token_total{component="orchestrator",'
        'model="deepseek-chat",operation="route_classification",status="success"} 2000'
    ) in metrics_text
    assert (
        'cloud_agent_llm_estimated_cost_usd_total{component="orchestrator",'
        'model="deepseek-chat",operation="route_classification",status="success"} 3.5'
    ) in metrics_text
    for forbidden in (
        "req_llm_cost_route",
        "hash_llm_cost_route",
        "tenant_a",
        "secret memory context",
        "user_test",
    ):
        assert forbidden not in metrics_text

    reset_metrics()


def test_graph_routes_to_fallback_and_support_nodes():
    async def _run():
        from core.workflow.graph_manager import AgentGraphManager

        graph = AgentGraphManager().build_graph()
        base_state = {
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": {"request_id": "req_graph_test"},
        }

        fallback_result = await graph.ainvoke(
            {**base_state, "messages": [("user", "今天天气怎么样？")]}
        )
        support_result = await graph.ainvoke(
            {**base_state, "messages": [("user", "我的 ECS 无法 SSH 连接")]}
        )

        return fallback_result, support_result

    fallback_result, support_result = asyncio.run(_run())

    assert fallback_result["metadata"]["handled_by"] == "fallback_agent"
    assert fallback_result["metadata"]["request_id"] == "req_graph_test"
    assert support_result["metadata"]["handled_by"] == "support_agent"
    assert support_result["metadata"]["request_id"] == "req_graph_test"

    from core.workflow.graph_manager import AgentGraphManager

    graph = AgentGraphManager().build_graph()
    assert "checkpoint_agent" in graph.nodes


def test_complex_multistep_request_routes_to_planner_agent():
    result = _route("先帮我查最近买了哪些机器，再分析哪些可以降配")

    assert result["next_agent"] == "planner_agent"
    assert result["metadata"]["primary_intent"] == "planning"
    assert result["metadata"]["planner_seed_agent"] == "billing_agent"
    assert result["metadata"]["planner_mode"] == "plan"
    assert result["metadata"]["planner_status"] == "planned"


def test_explicit_replan_request_routes_to_planner_agent():
    result = _route(
        "重新规划一下上面的任务",
        metadata={
            "planner_replan_requested": True,
            "planner_seed_agent": "support_agent",
            "planner_failure_reason": "上一轮信息不完整",
        },
    )

    assert result["next_agent"] == "planner_agent"
    assert result["metadata"]["primary_intent"] == "planning"
    assert result["metadata"]["planner_seed_agent"] == "support_agent"
    assert result["metadata"]["planner_mode"] == "replan"
    assert result["metadata"]["planner_status"] == "replanned"
