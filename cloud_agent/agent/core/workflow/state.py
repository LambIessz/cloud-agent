import operator
from typing import Annotated, Any, Literal, NotRequired, Sequence, TypedDict
from langchain_core.messages import BaseMessage
from core.workflow.context_manager import ContextBundle


class HumanCheckpoint(TypedDict, total=False):
    checkpoint_id: str
    status: Literal["pending", "confirmed", "rejected"]
    resume_agent: str
    risk_level: str
    action_summary: str
    source_query: str
    route_reason: str
    attempts: int
    created_at_ms: int
    updated_at_ms: int


class TaskPlanStep(TypedDict, total=False):
    step_id: str
    agent: str
    objective: str
    stop_conditions: list[str]
    fallback_agent: str


class TaskPlan(TypedDict, total=False):
    plan_id: str
    mode: Literal["plan", "replan"]
    status: Literal["planned", "replanned"]
    revision: int
    source_query: str
    goal: str
    target_agent: str
    followup_agent: str
    reason: str
    failure_reason: str
    replan_triggers: list[str]
    stop_conditions: list[str]
    steps: list[TaskPlanStep]
    created_at_ms: int
    updated_at_ms: int


class CollaborationFinding(TypedDict, total=False):
    agent: str
    stage: str
    summary: str
    resource_ids: list[str]
    status: str
    notes: list[str]
    created_at_ms: int
    updated_at_ms: int


class CollaborationState(TypedDict, total=False):
    collaboration_id: str
    mode: str
    status: str
    participants: list[str]
    findings: list[CollaborationFinding]
    conflicts: list[str]
    merged_summary: str
    reason: str
    stage: str
    created_at_ms: int
    updated_at_ms: int

class AgentState(TypedDict):
    """
    LangGraph 全局状态。
    负责在 Router、各个子 Agent 以及 Memory 之间传递信息。
    """
    # 消息记录，使用 operator.add 将新消息追加到列表末尾
    messages: Annotated[Sequence[BaseMessage], operator.add]
    
    # 决定下一步走向哪个节点的路由标记
    next_agent: str
    
    # 用户信息，用于鉴权和记忆隔离
    user_id: str
    tenant_id: str
    session_id: str
    
    # 注入的记忆信息 (长短期记忆提取出的背景上下文)
    memory_context: str
    context_bundle: NotRequired[ContextBundle | None]
    task_plan: NotRequired[TaskPlan | None]
    collaboration_state: NotRequired[CollaborationState | None]
    
    # 工具调用的附带信息或元数据
    metadata: dict[str, Any]

    # 人机介入检查点状态（可选）
    human_checkpoint: NotRequired[HumanCheckpoint | None]

class AgentOutput(TypedDict):
    """Agent 执行的标准输出格式。"""
    response: str
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any]
