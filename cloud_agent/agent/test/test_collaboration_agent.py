import asyncio

from langchain_core.messages import AIMessage

from agents.collaboration_agent import CollaborationSynthesisAgent
from core.workflow.collaboration_state import (
    append_collaboration_finding,
    finalize_collaboration_state,
    get_collaboration_state,
    seed_collaboration_state,
)


def test_collaboration_state_tracks_findings_and_resource_ids():
    metadata = seed_collaboration_state(
        {"request_id": "req_collab_state", "user_id_hash": "hash_collab_state"},
        participants=["billing_agent", "finops_agent", "collaboration_agent"],
        reason="billing to finops synthesis",
    )
    metadata = append_collaboration_finding(
        metadata,
        agent_name="billing_agent",
        summary="账单侧发现目标实例 i-bp123，最近一个月共有 2 台 ECS。",
        stage="billing",
    )
    metadata = append_collaboration_finding(
        metadata,
        agent_name="finops_agent",
        summary="成本侧确认 i-bp123 长期低负载，适合降配。",
        stage="finops",
    )

    collab_state = get_collaboration_state(metadata)
    assert collab_state is not None
    assert collab_state["mode"] == "billing_finops_synthesis"
    assert collab_state["participants"] == [
        "billing_agent",
        "finops_agent",
        "collaboration_agent",
    ]
    assert len(collab_state["findings"]) == 2
    assert collab_state["findings"][0]["resource_ids"] == ["i-bp123"]
    assert collab_state["findings"][1]["resource_ids"] == ["i-bp123"]


def test_collaboration_agent_merges_billing_and_finops_findings():
    async def _run():
        agent = CollaborationSynthesisAgent()
        metadata = seed_collaboration_state(
            {"request_id": "req_collab_agent", "user_id_hash": "hash_collab_agent"},
            participants=["billing_agent", "finops_agent", "collaboration_agent"],
            reason="billing to finops synthesis",
        )
        metadata = append_collaboration_finding(
            metadata,
            agent_name="billing_agent",
            summary="账单侧发现目标实例 i-bp123，最近一个月共有 2 台 ECS。",
            stage="billing",
        )
        metadata = append_collaboration_finding(
            metadata,
            agent_name="finops_agent",
            summary="成本侧确认 i-bp123 长期低负载，适合降配。",
            stage="finops",
        )
        state = {
            "messages": [
                ("user", "账单太高，帮我做一次降本合并分析"),
                AIMessage(content="billing summary"),
                AIMessage(content="finops summary"),
            ],
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": metadata,
        }
        return await agent(state)

    result = asyncio.run(_run())
    content = result["messages"][0].content

    assert "账单/资源侧" in content
    assert "成本分析侧" in content
    assert "交叉校验" in content
    assert "i-bp123" in content
    assert result["metadata"]["handled_by"] == "collaboration_agent"
    assert result["metadata"]["collaboration_state"]["status"] == "merged"


def test_graph_routes_finops_workflow_into_collaboration_node():
    from agents.collaboration_agent import CollaborationSynthesisAgent
    from core.workflow.collaboration_state import append_collaboration_finding, finalize_collaboration_state
    from core.workflow.graph_manager import AgentGraphManager

    class _BillingNode:
        async def __call__(self, state):
            metadata = append_collaboration_finding(
                state["metadata"],
                agent_name="billing_agent",
                summary="账单侧发现目标实例 i-bp123。",
                stage="billing",
            )
            return {
                "messages": [AIMessage(content="billing summary")],
                "metadata": metadata,
            }

    class _FinOpsNode:
        async def __call__(self, state):
            metadata = append_collaboration_finding(
                state["metadata"],
                agent_name="finops_agent",
                summary="成本侧发现 i-bp123 闲置，适合降配。",
                stage="finops",
            )
            return {
                "messages": [AIMessage(content="finops summary")],
                "metadata": metadata,
                "next_agent": "",
            }

    class _CollaborationNode:
        async def __call__(self, state):
            metadata = finalize_collaboration_state(
                state["metadata"],
                merged_summary="合并结论：i-bp123 可以做降配。",
            )
            metadata["handled_by"] = "collaboration_agent"
            return {
                "messages": [AIMessage(content="合并结论：i-bp123 可以做降配。")],
                "metadata": metadata,
            }

    async def _run():
        manager = AgentGraphManager(
            collaboration_node=CollaborationSynthesisAgent(),
        )
        manager.billing_node = _BillingNode()
        manager.finops_node = _FinOpsNode()
        manager.collaboration_node = _CollaborationNode()
        graph = manager.build_graph()
        state = {
            "messages": [("user", "账单太高，帮我降本")],
            "user_id": "user_test",
            "tenant_id": "default_tenant",
            "session_id": "session_test",
            "memory_context": "",
            "next_agent": "",
            "metadata": {},
        }
        return await graph.ainvoke(state)

    result = asyncio.run(_run())

    assert result["metadata"]["handled_by"] == "collaboration_agent"
    assert result["metadata"]["collaboration_state"]["status"] == "merged"
    assert "合并结论" in result["messages"][-1].content
