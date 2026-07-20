import asyncio

from agents.planner_agent import TaskPlannerNode
from core.workflow.context_manager import build_context_bundle


def _build_state(query: str, metadata=None, task_plan=None):
    metadata = metadata or {}
    context_bundle = build_context_bundle(
        query=query,
        history=[],
        preferences=[],
        metadata=metadata,
        agent_names=[
            "orchestrator",
            "billing_agent",
            "finops_agent",
            "support_agent",
            "recommendation_agent",
            "product_agent",
        ],
    )
    state = {
        "messages": [("user", query)],
        "user_id": "user_test",
        "tenant_id": "default_tenant",
        "session_id": "session_test",
        "memory_context": "",
        "context_bundle": context_bundle,
        "next_agent": "",
        "metadata": metadata,
    }
    if task_plan is not None:
        state["task_plan"] = task_plan
    return state


def test_planner_builds_multistep_plan_and_updates_context_bundle():
    async def _run():
        planner = TaskPlannerNode()
        state = _build_state(
            "先帮我查最近买了哪些机器，再分析哪些可以降配",
            metadata={
                "request_id": "req_plan_test",
                "user_id_hash": "hash_plan_test",
                "planner_seed_agent": "billing_agent",
            },
        )
        return await planner(state)

    result = asyncio.run(_run())
    plan = result["task_plan"]

    assert result["next_agent"] == "billing_agent"
    assert plan["mode"] == "plan"
    assert plan["status"] == "planned"
    assert plan["target_agent"] == "billing_agent"
    assert plan["followup_agent"] == "finops_agent"
    assert len(plan["steps"]) == 2
    assert result["metadata"]["planner_mode"] == "plan"
    assert result["metadata"]["planner_target_agent"] == "billing_agent"
    assert result["metadata"]["planner_followup_agent"] == "finops_agent"
    assert result["metadata"]["planner_step_count"] == 2
    assert result["context_bundle"]["task_plan"]["target_agent"] == "billing_agent"
    assert "【任务规划】" in result["context_bundle"]["agent_contexts"]["billing_agent"]
    assert "finops_agent" in result["context_bundle"]["agent_contexts"]["finops_agent"]


def test_planner_replans_from_previous_plan_revision():
    async def _run():
        planner = TaskPlannerNode()
        state = _build_state(
            "重新规划一下这次账单和降配分析",
            metadata={
                "request_id": "req_replan_test",
                "user_id_hash": "hash_replan_test",
                "planner_seed_agent": "billing_agent",
                "planner_replan_requested": True,
                "planner_failure_reason": "上一轮工具返回空结果",
            },
            task_plan={
                "plan_id": "plan_old",
                "mode": "plan",
                "status": "planned",
                "revision": 2,
                "target_agent": "billing_agent",
                "followup_agent": "finops_agent",
                "steps": [],
            },
        )
        return await planner(state)

    result = asyncio.run(_run())
    plan = result["task_plan"]

    assert plan["mode"] == "replan"
    assert plan["status"] == "replanned"
    assert plan["revision"] == 3
    assert plan["failure_reason"] == "上一轮工具返回空结果"
    assert result["metadata"]["planner_mode"] == "replan"
    assert result["metadata"]["planner_status"] == "replanned"
    assert result["metadata"]["planner_revision"] == 3
    assert result["metadata"]["planner_replan_requested"] is False
    assert result["context_bundle"]["task_plan"]["status"] == "replanned"
