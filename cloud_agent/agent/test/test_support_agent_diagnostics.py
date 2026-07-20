import asyncio
import json

from agents.support_agent import SupportAgentNode


class _FakeTool:
    def __init__(self, name, responder):
        self.name = name
        self.description = f"{name} description"
        self.calls = []
        self._responder = responder

    async def ainvoke(self, args, config=None):
        self.calls.append({"args": args, "config": config})
        result = self._responder(args, config)
        if asyncio.iscoroutine(result):
            return await result
        return result


class _FakeRegistry:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    async def get_tools_for_agent(
        self,
        agent_name,
        *,
        request_id="unknown",
        user_id_hash="unknown",
        query=None,
        max_tools=None,
    ):
        self.calls.append(
            {
                "agent_name": agent_name,
                "request_id": request_id,
                "user_id_hash": user_id_hash,
                "query": query,
                "max_tools": max_tools,
            }
        )
        return list(self.tools)


def test_support_agent_runs_live_diagnostic_loop_with_instance_id():
    query_tool = _FakeTool(
        "query_user_instances",
        lambda _args, _config: json.dumps(
            {
                "status": "success",
                "data": [
                    {
                        "instance_id": "i-bp123",
                        "status": "Running",
                        "region_id": "cn-hangzhou",
                        "public_ip": "1.2.3.4",
                    }
                ],
                "user_message": "ok",
            },
            ensure_ascii=False,
        ),
    )
    usage_tool = _FakeTool(
        "analyze_instance_usage",
        lambda _args, _config: json.dumps(
            {
                "status": "success",
                "data": {
                    "instance_id": "i-bp123",
                    "metrics_7d_avg": {
                        "cpu_usage_percent": 82.5,
                        "memory_usage_percent": 91.2,
                        "network_out_bandwidth_mbps": 12.4,
                    },
                    "diagnosis": "RESOURCES_TIGHT",
                },
                "user_message": "ok",
            },
            ensure_ascii=False,
        ),
    )
    registry = _FakeRegistry([query_tool, usage_tool])
    agent = SupportAgentNode(tool_registry=registry)

    async def _run():
        state = {
            "messages": [("user", "我的 ECS i-bp123 无法 SSH 连接")],
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": {"request_id": "req_support", "user_id_hash": "hash_support"},
        }
        return await agent(state)

    result = asyncio.run(_run())
    content = result["messages"][0].content

    assert registry.calls[0]["agent_name"] == "support"
    assert query_tool.calls[0]["args"]["user_id"] == "user_test"
    assert usage_tool.calls[0]["args"]["instance_id"] == "i-bp123"
    assert "实时诊断证据" in content
    assert "i-bp123" in content
    assert "RESOURCES_TIGHT" in content
    assert result["metadata"]["handled_by"] == "support_agent"
    assert result["metadata"]["support_diagnostics"]["status"] == "success"
    assert result["metadata"]["support_diagnostics"]["evidence_count"] == 2


def test_support_agent_skips_live_diagnostics_without_instance_id():
    registry = _FakeRegistry([])
    agent = SupportAgentNode(tool_registry=registry)

    async def _run():
        state = {
            "messages": [("user", "我的 ECS 无法 SSH 连接")],
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": {"request_id": "req_support", "user_id_hash": "hash_support"},
        }
        return await agent(state)

    result = asyncio.run(_run())
    content = result["messages"][0].content

    assert registry.calls == []
    assert "instance_id" in content
    assert result["metadata"]["support_diagnostics"]["status"] == "needs_instance_id"
