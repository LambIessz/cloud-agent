import asyncio
import json
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

APP_DIR = Path(__file__).resolve().parents[2] / "app"
AGENT_DIR = Path(__file__).resolve().parents[1]
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import service.chat_service as chat_service
from agents.checkpoint_agent import CheckpointAgentNode
from core.workflow.human_checkpoint import build_checkpoint_record


class _UnavailableCache:
    available = False


class _CheckpointShortTerm:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        return []


class _CheckpointLongTerm:
    available = False


class _CheckpointMemory:
    def __init__(self):
        self.short_term = _CheckpointShortTerm()
        self.long_term = _CheckpointLongTerm()
        self.loaded_checkpoint = None
        self.saved_checkpoints = []
        self.cleared_checkpoints = []
        self.saved_turns = []
        self.background_extract_calls = 0

    async def get_session_checkpoint(self, _user_id, _session_id):
        return self.loaded_checkpoint

    async def save_session_checkpoint(self, user_id, session_id, checkpoint):
        self.saved_checkpoints.append((user_id, session_id, checkpoint))
        self.loaded_checkpoint = checkpoint

    async def clear_session_checkpoint(self, user_id, session_id):
        self.cleared_checkpoints.append((user_id, session_id))
        self.loaded_checkpoint = None

    async def save_conversation(self, user_id, session_id, turn):
        self.saved_turns.append((user_id, session_id, turn))

    async def background_extract(self, *_args, **_kwargs):
        self.background_extract_calls += 1
        return []


class _CheckpointGraph:
    def __init__(self, *, status: str, response_text: str):
        self.status = status
        self.response_text = response_text
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        metadata = state.get("metadata", {})
        checkpoint = metadata.get("human_checkpoint")
        if not isinstance(checkpoint, dict):
            query = ""
            messages = state.get("messages") or []
            if messages:
                last_message = messages[-1]
                if isinstance(last_message, tuple) and len(last_message) > 1:
                    query = str(last_message[1])
                else:
                    query = str(getattr(last_message, "content", last_message))
            checkpoint = build_checkpoint_record(
                query=query,
                resume_agent="support_agent",
                route_reason="检测到高风险动作",
            )
        else:
            checkpoint = dict(checkpoint)
        checkpoint["status"] = self.status
        return {
            "messages": [AIMessage(content=self.response_text)],
            "metadata": {"human_checkpoint": checkpoint},
        }


def _payloads(chunks: list[str]) -> list[dict]:
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def _checkpoint_state(query: str, checkpoint: dict | None = None):
    state = {
        "messages": [("user", query)],
        "user_id": "user_test",
        "tenant_id": "default_tenant",
        "session_id": "session_test",
        "memory_context": "",
        "next_agent": "",
        "metadata": {"request_id": "req_checkpoint_test"},
    }
    if checkpoint is not None:
        state["metadata"]["human_checkpoint"] = checkpoint
    return state


def test_checkpoint_agent_prompts_for_confirmation_for_high_risk_action():
    async def _run():
        agent = CheckpointAgentNode()
        return await agent(_checkpoint_state("帮我重启 ECS i-bp123"))

    result = asyncio.run(_run())
    checkpoint = result["metadata"]["human_checkpoint"]

    assert result["metadata"]["handled_by"] == "checkpoint_agent"
    assert checkpoint["status"] == "pending"
    assert checkpoint["resume_agent"] == "support_agent"
    assert checkpoint["source_query"] == "帮我重启 ECS i-bp123"
    assert result["messages"][0].content
    assert result.get("next_agent") in (None, "")


def test_checkpoint_agent_confirms_and_returns_resume_agent():
    checkpoint = build_checkpoint_record(
        query="帮我重启 ECS i-bp123",
        resume_agent="support_agent",
        route_reason="检测到重启操作",
    )

    async def _run():
        agent = CheckpointAgentNode()
        return await agent(_checkpoint_state("确认", checkpoint=checkpoint))

    result = asyncio.run(_run())
    updated = result["metadata"]["human_checkpoint"]

    assert result["next_agent"] == "support_agent"
    assert updated["status"] == "confirmed"
    assert updated["attempts"] == checkpoint["attempts"] + 1
    assert updated["source_query"] == "帮我重启 ECS i-bp123"
    assert result["messages"][0].content


def test_checkpoint_agent_rejects_high_risk_action():
    checkpoint = build_checkpoint_record(
        query="帮我重启 ECS i-bp123",
        resume_agent="support_agent",
        route_reason="检测到重启操作",
    )

    async def _run():
        agent = CheckpointAgentNode()
        return await agent(_checkpoint_state("取消", checkpoint=checkpoint))

    result = asyncio.run(_run())
    updated = result["metadata"]["human_checkpoint"]

    assert updated["status"] == "rejected"
    assert updated["attempts"] == checkpoint["attempts"] + 1
    assert result.get("next_agent") in (None, "")
    assert result["messages"][0].content


def test_stream_chat_saves_pending_checkpoint_and_skips_background_extract(monkeypatch):
    async def _run():
        original_graph = chat_service.graph
        original_memory = chat_service.memory
        original_cache = chat_service.semantic_cache
        monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "false")
        memory = _CheckpointMemory()
        graph = _CheckpointGraph(status="pending", response_text="请先确认是否继续执行")
        chat_service.graph = graph
        chat_service.memory = memory
        chat_service.semantic_cache = _UnavailableCache()
        try:
            chunks = []
            async for chunk in chat_service.stream_chat(
                "帮我重启 ECS i-bp123",
                "plain_user",
                "session_1",
                request_id="req_checkpoint_pending",
                request_tenant_id="tenant_a",
            ):
                chunks.append(chunk)
            return graph, memory, _payloads(chunks)
        finally:
            chat_service.graph = original_graph
            chat_service.memory = original_memory
            chat_service.semantic_cache = original_cache

    graph, memory, payloads = asyncio.run(_run())

    assert graph.calls == 1
    assert memory.saved_checkpoints
    assert memory.saved_checkpoints[0][2]["status"] == "pending"
    assert memory.cleared_checkpoints == []
    assert memory.background_extract_calls == 0
    assert payloads[-1]["event_type"] == "done"
    assert payloads[-1]["request_id"] == "req_checkpoint_pending"


def test_stream_chat_clears_checkpoint_after_confirmation(monkeypatch):
    async def _run():
        original_graph = chat_service.graph
        original_memory = chat_service.memory
        original_cache = chat_service.semantic_cache
        monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "false")
        memory = _CheckpointMemory()
        memory.loaded_checkpoint = build_checkpoint_record(
            query="帮我重启 ECS i-bp123",
            resume_agent="support_agent",
            route_reason="检测到重启操作",
        )
        graph = _CheckpointGraph(status="confirmed", response_text="已确认，继续处理")
        chat_service.graph = graph
        chat_service.memory = memory
        chat_service.semantic_cache = _UnavailableCache()
        try:
            chunks = []
            async for chunk in chat_service.stream_chat(
                "确认",
                "plain_user",
                "session_1",
                request_id="req_checkpoint_confirmed",
                request_tenant_id="tenant_a",
            ):
                chunks.append(chunk)
            return graph, memory, _payloads(chunks)
        finally:
            chat_service.graph = original_graph
            chat_service.memory = original_memory
            chat_service.semantic_cache = original_cache

    graph, memory, payloads = asyncio.run(_run())

    assert graph.calls == 1
    assert memory.saved_checkpoints == []
    assert memory.cleared_checkpoints
    assert memory.background_extract_calls == 0
    assert payloads[-1]["event_type"] == "done"
    assert payloads[-1]["request_id"] == "req_checkpoint_confirmed"
