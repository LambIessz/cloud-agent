import asyncio
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
AGENT_DIR = Path(__file__).resolve().parents[1]
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import service.chat_service as chat_service
from core.workflow.context_manager import build_context_bundle, select_agent_memory_context


class _UnavailableCache:
    available = False


class _ShortTerm:
    available = True

    def __init__(self, history):
        self._history = history

    async def get_messages(self, *_args, **_kwargs):
        return list(self._history)


class _LongTerm:
    available = True

    def __init__(self, preferences):
        self._preferences = preferences

    async def retrieve_relevant(self, *_args, **_kwargs):
        return list(self._preferences)


class _ContextMemory:
    def __init__(self, history, preferences):
        self.short_term = _ShortTerm(history)
        self.long_term = _LongTerm(preferences)
        self.saved_turns = []

    async def save_conversation(self, user_id, session_id, turn):
        self.saved_turns.append((user_id, session_id, turn))


class _CaptureGraph:
    def __init__(self):
        self.captured_state = None

    async def ainvoke(self, state, config=None):
        from langchain_core.messages import AIMessage

        self.captured_state = state
        return {"messages": [AIMessage(content="ok")]}


def _sample_history():
    return [
        {"role": "user", "content": "我在排查 ECS SSH 失败"},
        {"role": "assistant", "content": "先看安全组和公网 IP"},
        {"role": "user", "content": "RDS 连接也慢"},
        {"role": "assistant", "content": "再看白名单和端口"},
        {"role": "user", "content": "账单太高想降本"},
        {"role": "assistant", "content": "看成本优化工作流"},
        {"role": "user", "content": "继续上一轮的建议"},
        {"role": "assistant", "content": "继续"},
        {"role": "user", "content": "再补充一轮上下文"},
        {"role": "assistant", "content": "继续"},
        {"role": "user", "content": "最新一轮"},
        {"role": "assistant", "content": "收到"},
    ]


def test_build_context_bundle_uses_agent_profiles_and_summaries():
    history = _sample_history()
    preferences = [
        "language: Chinese",
        "region: Shanghai",
        "focus: ECS",
        "tone: concise",
    ]
    metadata = {
        "human_checkpoint": {
            "status": "pending",
            "action_summary": "重启 ECS i-bp123",
        },
        "support_diagnostics": {
            "summary": "目标实例 i-bp123 状态 Running",
            "evidence_count": 2,
        },
        "is_finops_workflow": True,
        "route_reason": "检测到重启操作",
    }

    bundle = build_context_bundle(
        query="帮我重启 ECS i-bp123",
        history=history,
        preferences=preferences,
        metadata=metadata,
    )

    orchestrator = bundle["agent_contexts"]["orchestrator"]
    recommendation = bundle["agent_contexts"]["recommendation_agent"]

    assert bundle["version"] == 1
    assert bundle["summary"]["omitted_messages"] == 4
    assert "系统约束与控制信息" in orchestrator
    assert "当前会话存在待确认的高风险动作" in orchestrator
    assert "历史摘要" in orchestrator
    assert "ECS" in orchestrator or "RDS" in orchestrator
    assert recommendation.count("User:") >= orchestrator.count("User:")
    assert "长期背景" in recommendation


def test_select_agent_memory_context_prefers_bundle_over_legacy_memory_context():
    state = {
        "memory_context": "legacy context",
        "context_bundle": {
            "default_agent": "orchestrator",
            "agent_contexts": {
                "orchestrator": "orch context",
                "product_agent": "product context",
            },
        },
    }

    assert select_agent_memory_context(state, "product_agent") == "product context"
    assert select_agent_memory_context(state, "orchestrator") == "orch context"
    assert select_agent_memory_context({"memory_context": "legacy context"}, "fallback_agent") == "legacy context"


def test_memory_manager_build_context_bundle_uses_memory_layers():
    from core.memory.memory_manager import MemoryManager

    history = _sample_history()
    preferences = ["language: Chinese", "region: Shanghai", "focus: ECS"]
    manager = MemoryManager()
    manager.short_term = _ShortTerm(history)
    manager.long_term = _LongTerm(preferences)

    bundle = asyncio.run(
        manager.build_context_bundle(
            "user_test",
            "session_test",
            "帮我重启 ECS i-bp123",
            metadata={"route_reason": "检测到重启操作"},
        )
    )

    assert bundle["summary"]["history_messages"] == len(history)
    assert bundle["agent_contexts"]["orchestrator"]
    assert "历史摘要" in bundle["agent_contexts"]["orchestrator"]


def test_stream_chat_attaches_context_bundle_to_graph_state(monkeypatch):
    history = _sample_history()
    preferences = ["language: Chinese", "region: Shanghai", "focus: ECS"]

    async def _run():
        original_graph = chat_service.graph
        original_memory = chat_service.memory
        original_cache = chat_service.semantic_cache
        monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "false")
        memory = _ContextMemory(history, preferences)
        graph = _CaptureGraph()
        chat_service.graph = graph
        chat_service.memory = memory
        chat_service.semantic_cache = _UnavailableCache()
        try:
            chunks = []
            async for chunk in chat_service.stream_chat(
                "帮我重启 ECS i-bp123",
                "plain_user",
                "session_1",
                request_id="req_context_bundle",
                request_tenant_id="tenant_a",
            ):
                chunks.append(chunk)
            return graph, chunks
        finally:
            chat_service.graph = original_graph
            chat_service.memory = original_memory
            chat_service.semantic_cache = original_cache

    graph, chunks = asyncio.run(_run())
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))

    assert graph.captured_state is not None
    assert graph.captured_state["memory_context"]
    assert graph.captured_state["context_bundle"]["agent_contexts"]["orchestrator"] == graph.captured_state["memory_context"]
    assert graph.captured_state["context_bundle"]["agent_contexts"]["recommendation_agent"] != graph.captured_state["memory_context"]
    assert payloads[-1]["event_type"] == "done"
    assert payloads[-1]["request_id"] == "req_context_bundle"


def test_build_context_bundle_includes_planner_agent_profile():
    bundle = build_context_bundle(
        query="先查账单再给降配建议",
        history=[],
        preferences=[],
        metadata={},
    )

    assert "planner_agent" in bundle["agent_contexts"]
    assert bundle["summary"]["agent_profiles"]["planner_agent"]["budget_tokens"] > 0
