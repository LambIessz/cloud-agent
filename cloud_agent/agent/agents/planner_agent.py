from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Mapping

from core.workflow.context_manager import ContextBundle
from core.workflow.event_log import build_event, emit_event, now_ms
from core.workflow.request_context import ensure_request_metadata, get_request_id
from core.workflow.state import AgentState, TaskPlan, TaskPlanStep


logger = logging.getLogger(__name__)

_PLAN_HINTS = (
    "先",
    "然后",
    "再",
    "同时",
    "并且",
    "顺便",
    "接着",
    "之后",
    "步骤",
    "分步",
    "拆解",
    "计划",
    "方案",
    "流程",
    "规划",
)

_REPLAN_HINTS = (
    "重来",
    "重新",
    "再规划",
    "换个方案",
    "修正",
    "失败",
    "报错",
    "错误",
    "不对",
    "retry",
    "replan",
)

_BILLING_HINTS = (
    "账单",
    "订单",
    "购买",
    "买了哪些",
    "名下",
    "实例",
    "资源",
)

_FINOPS_HINTS = (
    "降本",
    "降配",
    "成本",
    "闲置",
    "省钱",
    "优化成本",
    "费用太高",
    "预算",
)

_SUPPORT_HINTS = (
    "ssh",
    "端口",
    "连接失败",
    "无法连接",
    "无法访问",
    "故障",
    "排查",
    "异常",
    "cpu",
    "内存",
    "启动失败",
    "公网ip",
    "安全组",
)

_RECOMMENDATION_HINTS = (
    "推荐",
    "选型",
    "配置",
    "买哪",
    "哪款",
    "高并发",
    "预算",
    "实例规格",
)

_PRODUCT_HINTS = (
    "是什么",
    "介绍",
    "区别",
    "规格",
    "文档",
    "限制",
    "上限",
    "配额",
    "vpc",
    "ecs",
    "rds",
)

_PROMOTION_HINTS = (
    "推广",
    "返佣",
    "海报",
    "活动",
    "链接",
    "物料",
    "佣金",
)

_VALID_AGENTS = (
    "billing_agent",
    "support_agent",
    "recommendation_agent",
    "product_agent",
    "promotion_agent",
    "finops_agent",
    "fallback_agent",
)


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _first_user_text(state: AgentState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""
    last_message = messages[-1]
    if isinstance(last_message, tuple):
        return str(last_message[1])
    if hasattr(last_message, "content"):
        return str(last_message.content)
    return str(last_message)


def _current_plan(state: AgentState) -> dict[str, Any] | None:
    plan = state.get("task_plan")
    return plan if isinstance(plan, dict) else None


def _score_agent(text: str, hints: tuple[str, ...]) -> int:
    score = 0
    for hint in hints:
        if hint in text:
            score += 1
    return score


def _infer_seed_agent(
    text: str,
    metadata: Mapping[str, Any],
    current_plan: Mapping[str, Any] | None,
) -> str:
    seed_agent = str(metadata.get("planner_seed_agent") or "").strip()
    if seed_agent in _VALID_AGENTS:
        return seed_agent

    if current_plan:
        current_target = str(current_plan.get("target_agent") or "").strip()
        if current_target in _VALID_AGENTS:
            return current_target

    scored_agents = [
        ("billing_agent", _score_agent(text, _BILLING_HINTS)),
        ("support_agent", _score_agent(text, _SUPPORT_HINTS)),
        ("recommendation_agent", _score_agent(text, _RECOMMENDATION_HINTS)),
        ("product_agent", _score_agent(text, _PRODUCT_HINTS)),
        ("promotion_agent", _score_agent(text, _PROMOTION_HINTS)),
        ("finops_agent", _score_agent(text, _FINOPS_HINTS)),
    ]
    scored_agents.sort(key=lambda item: (-item[1], _VALID_AGENTS.index(item[0])))
    best_agent, best_score = scored_agents[0]
    if best_score > 0:
        if best_agent == "finops_agent":
            return "billing_agent"
        return best_agent

    primary_intent = str(metadata.get("primary_intent") or "").strip()
    intent_to_agent = {
        "billing": "billing_agent",
        "finops": "billing_agent",
        "support": "support_agent",
        "recommendation": "recommendation_agent",
        "product": "product_agent",
        "promotion": "promotion_agent",
    }
    return intent_to_agent.get(primary_intent, "fallback_agent")


def _infer_followup_agent(
    text: str,
    seed_agent: str,
    metadata: Mapping[str, Any],
    current_plan: Mapping[str, Any] | None,
) -> str | None:
    if current_plan:
        previous_followup = str(current_plan.get("followup_agent") or "").strip()
        if previous_followup in _VALID_AGENTS:
            return previous_followup

    if seed_agent == "billing_agent":
        if metadata.get("is_finops_workflow") or _contains_any(text, _FINOPS_HINTS):
            return "finops_agent"
        if _contains_any(text, _SUPPORT_HINTS):
            return "support_agent"
        if _contains_any(text, _RECOMMENDATION_HINTS):
            return "recommendation_agent"
    if seed_agent == "support_agent":
        if metadata.get("is_finops_workflow") or _contains_any(text, _FINOPS_HINTS):
            return "finops_agent"
        if _contains_any(text, _PRODUCT_HINTS):
            return "product_agent"
    if seed_agent == "recommendation_agent" and _contains_any(text, _PRODUCT_HINTS):
        return "product_agent"
    if seed_agent == "product_agent" and _contains_any(text, _RECOMMENDATION_HINTS):
        return "recommendation_agent"
    if seed_agent == "promotion_agent" and _contains_any(text, _PRODUCT_HINTS):
        return "product_agent"
    return None


def _step_profile(agent: str, *, followup_agent: str | None = None, replan_reason: str = "") -> tuple[str, list[str], str]:
    if agent == "billing_agent":
        objective = "先确认用户名下订单、实例或账单证据，为后续分析建立事实基础。"
        stop_conditions = [
            "已拿到最近购买或名下资源清单",
            "如果缺少实例 ID，先向用户补充",
        ]
        fallback_agent = "support_agent"
    elif agent == "support_agent":
        objective = "先定位实例、网络、安全组或运行状态，收集只读诊断证据。"
        stop_conditions = [
            "已确认具体实例或明确缺少实例 ID",
            "已经收集到网络/日志/监控中的关键证据",
        ]
        fallback_agent = "fallback_agent"
    elif agent == "recommendation_agent":
        objective = "先根据业务约束、预算和并发需求收敛候选配置。"
        stop_conditions = [
            "已明确核心约束和预算",
            "已缩小到可对比的配置集合",
        ]
        fallback_agent = "product_agent"
    elif agent == "product_agent":
        objective = "先把产品规格、限制和适用场景讲清楚。"
        stop_conditions = [
            "已明确用户想看的产品或能力",
            "已解释关键限制和适用边界",
        ]
        fallback_agent = "recommendation_agent"
    elif agent == "promotion_agent":
        objective = "先确认推广对象、渠道和物料诉求，再生成可投放内容。"
        stop_conditions = [
            "已明确推广产品和受众",
            "已收敛到可以直接产出的物料方向",
        ]
        fallback_agent = "product_agent"
    elif agent == "finops_agent":
        objective = "先基于实例、账单和监控证据判断是否存在可优化的成本空间。"
        stop_conditions = [
            "已拿到足够的成本证据",
            "已确认是否存在闲置或降配空间",
        ]
        fallback_agent = "billing_agent"
    else:
        objective = "先把任务拆成可执行步骤，再按最安全的路径往下推进。"
        stop_conditions = [
            "已明确首要目标",
            "已确认当前信息是否足够",
        ]
        fallback_agent = "fallback_agent"

    if replan_reason:
        objective = f"结合上次失败原因重新拆解：{replan_reason}"
        stop_conditions = list(stop_conditions) + ["已消化上次失败原因并调整路线"]

    if followup_agent:
        stop_conditions = list(stop_conditions) + [f"首轮结束后交给 {followup_agent} 继续处理"]

    return objective, stop_conditions, fallback_agent


def _build_plan_note(plan: TaskPlan) -> str:
    lines = [
        "【任务规划】",
        f"- 模式：{plan.get('mode') or 'plan'}",
        f"- 目标：{plan.get('goal') or '任务拆解'}",
        f"- 首个执行：{plan.get('target_agent') or 'fallback_agent'}",
    ]
    followup_agent = str(plan.get("followup_agent") or "").strip()
    if followup_agent:
        lines.append(f"- 后续：{followup_agent}")
    lines.append("- 子任务：")
    for index, step in enumerate(plan.get("steps", []), start=1):
        if not isinstance(step, Mapping):
            continue
        agent = str(step.get("agent") or "unknown").strip()
        objective = str(step.get("objective") or "").strip()
        lines.append(f"  {index}. {agent}：{objective}")
    stop_conditions = plan.get("stop_conditions", [])
    if stop_conditions:
        lines.append("- 停止条件：")
        for item in stop_conditions:
            lines.append(f"  - {item}")
    failure_reason = str(plan.get("failure_reason") or "").strip()
    if failure_reason:
        lines.append(f"- 重规划原因：{failure_reason}")
    return "\n".join(lines)


def _merge_plan_into_bundle(bundle: Mapping[str, Any] | None, plan: TaskPlan) -> ContextBundle:
    updated_bundle: ContextBundle
    if isinstance(bundle, Mapping):
        updated_bundle = copy.deepcopy(dict(bundle))  # type: ignore[assignment]
    else:
        updated_bundle = {
            "version": 1,
            "default_agent": "planner_agent",
            "query": str(plan.get("source_query") or ""),
            "summary": {},
            "sections": [],
            "profiles": {},
            "agent_contexts": {},
        }

    agent_contexts = dict(updated_bundle.get("agent_contexts") or {})
    note = _build_plan_note(plan)
    target_agent = str(plan.get("target_agent") or "").strip()
    followup_agent = str(plan.get("followup_agent") or "").strip()
    for agent_name in [target_agent, followup_agent]:
        if not agent_name:
            continue
        existing = str(agent_contexts.get(agent_name) or "").strip()
        agent_contexts[agent_name] = f"{existing}\n\n{note}".strip() if existing else note
    if not agent_contexts and target_agent:
        agent_contexts[target_agent] = note
    updated_bundle["agent_contexts"] = agent_contexts
    updated_bundle["task_plan"] = plan
    summary = dict(updated_bundle.get("summary") or {})
    summary["task_plan"] = {
        "mode": plan.get("mode"),
        "status": plan.get("status"),
        "revision": plan.get("revision"),
        "target_agent": target_agent,
        "followup_agent": followup_agent or None,
        "step_count": len(plan.get("steps", [])),
    }
    updated_bundle["summary"] = summary
    return updated_bundle


def _should_use_planner(text: str, metadata: Mapping[str, Any], seed_agent: str) -> bool:
    if str(metadata.get("planner_replan_requested") or "").lower() in {"1", "true", "yes", "on"}:
        return True
    current_plan = metadata.get("task_plan")
    if isinstance(current_plan, Mapping):
        status = str(current_plan.get("status") or "").lower()
        if status in {"failed", "replan_requested", "replanned"}:
            return True
    normalized = _normalize(text)
    if not _contains_any(normalized, _PLAN_HINTS):
        return False
    if seed_agent == "fallback_agent":
        return False
    return True


class TaskPlannerNode:
    """Lightweight deterministic planner for multi-step or replan requests."""

    def _build_task_plan(
        self,
        *,
        query: str,
        metadata: Mapping[str, Any],
        seed_agent: str,
        followup_agent: str | None,
        current_plan: Mapping[str, Any] | None,
        mode: str,
    ) -> TaskPlan:
        replan_reason = str(metadata.get("planner_failure_reason") or "").strip()
        objective, stop_conditions, fallback_agent = _step_profile(
            seed_agent,
            followup_agent=followup_agent,
            replan_reason=replan_reason if mode == "replan" else "",
        )
        steps: list[TaskPlanStep] = [
            {
                "step_id": "step_1",
                "agent": seed_agent,
                "objective": objective,
                "stop_conditions": stop_conditions,
                "fallback_agent": fallback_agent,
            }
        ]
        if followup_agent and followup_agent != seed_agent:
            followup_objective, followup_stop_conditions, followup_fallback = _step_profile(
                followup_agent,
                replan_reason=replan_reason if mode == "replan" else "",
            )
            steps.append(
                {
                    "step_id": "step_2",
                    "agent": followup_agent,
                    "objective": followup_objective,
                    "stop_conditions": followup_stop_conditions,
                    "fallback_agent": followup_fallback,
                }
            )

        previous_revision = 0
        if current_plan and isinstance(current_plan.get("revision"), int):
            previous_revision = int(current_plan.get("revision") or 0)

        plan: TaskPlan = {
            "plan_id": f"plan_{uuid.uuid4().hex[:12]}",
            "mode": "replan" if mode == "replan" else "plan",
            "status": "replanned" if mode == "replan" else "planned",
            "revision": previous_revision + 1 if mode == "replan" else 1,
            "source_query": query.strip(),
            "goal": query.strip()[:160] or "任务拆解",
            "target_agent": seed_agent,
            "reason": (
                f"检测到{seed_agent}相关请求需要先拆解再执行"
                if mode == "plan"
                else f"基于上次失败原因重新拆解{seed_agent}相关请求"
            ),
            "replan_triggers": [
                "用户明确要求重新规划",
                "关键证据缺失或工具返回错误",
                "当前步骤无法收敛到明确结论",
            ],
            "stop_conditions": [
                "已经拿到足够证据可以继续执行或回答",
                "如果关键证据缺失，先向用户补齐",
                "如果执行到高风险动作，先走人工确认",
            ],
            "steps": steps,
            "created_at_ms": now_ms(),
            "updated_at_ms": now_ms(),
        }
        if followup_agent and followup_agent != seed_agent:
            plan["followup_agent"] = followup_agent
        if mode == "replan" and replan_reason:
            plan["failure_reason"] = replan_reason
        return plan

    def _last_user_message(self, state: AgentState) -> str:
        return _first_user_text(state)

    def _should_plan(self, query: str, metadata: Mapping[str, Any], seed_agent: str) -> bool:
        if str(metadata.get("planner_replan_requested") or "").lower() in {"1", "true", "yes", "on"}:
            return True
        return _should_use_planner(query, metadata, seed_agent)

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        query = self._last_user_message(state)
        metadata = ensure_request_metadata(state.get("metadata", {}))
        current_plan = _current_plan(state)

        seed_agent = _infer_seed_agent(_normalize(query), metadata, current_plan)
        if not self._should_plan(query, metadata, seed_agent):
            seed_agent = seed_agent if seed_agent in _VALID_AGENTS else "fallback_agent"

        followup_agent = _infer_followup_agent(_normalize(query), seed_agent, metadata, current_plan)
        mode = "replan" if str(metadata.get("planner_replan_requested") or "").lower() in {"1", "true", "yes", "on"} else "plan"
        plan = self._build_task_plan(
            query=query,
            metadata=metadata,
            seed_agent=seed_agent,
            followup_agent=followup_agent,
            current_plan=current_plan,
            mode=mode,
        )

        updated_metadata = dict(metadata)
        updated_metadata["planner_mode"] = plan["mode"]
        updated_metadata["planner_status"] = plan["status"]
        updated_metadata["planner_target_agent"] = plan["target_agent"]
        if plan.get("followup_agent"):
            updated_metadata["planner_followup_agent"] = plan["followup_agent"]
        else:
            updated_metadata.pop("planner_followup_agent", None)
        updated_metadata["planner_reason"] = plan["reason"]
        updated_metadata["planner_revision"] = plan["revision"]
        updated_metadata["planner_step_count"] = len(plan.get("steps", []))
        if plan.get("failure_reason"):
            updated_metadata["planner_failure_reason"] = plan["failure_reason"]
        else:
            updated_metadata.pop("planner_failure_reason", None)
        updated_metadata["planner_replan_requested"] = False
        updated_metadata.pop("planner_requested", None)

        updated_bundle = _merge_plan_into_bundle(state.get("context_bundle"), plan)

        request_id = get_request_id(updated_metadata)
        user_id_hash = str(updated_metadata.get("user_id_hash", "unknown"))
        emit_event(
            build_event(
                event_type="task_plan",
                request_id=request_id,
                user_id_hash=user_id_hash,
                component="planner_agent",
                operation="plan",
                status=plan["status"],
                route_to=plan["target_agent"],
                plan_mode=plan["mode"],
                step_count=len(plan.get("steps", [])),
                followup_agent=plan.get("followup_agent"),
            )
        )
        logger.info(
            "PlannerAgent request_id=%s mode=%s target=%s followup=%s steps=%d",
            request_id,
            plan["mode"],
            plan["target_agent"],
            plan.get("followup_agent") or "-",
            len(plan.get("steps", [])),
        )

        return {
            "task_plan": plan,
            "metadata": updated_metadata,
            "context_bundle": updated_bundle,
            "next_agent": plan["target_agent"],
        }
