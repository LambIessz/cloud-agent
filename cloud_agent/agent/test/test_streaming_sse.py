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


class _UnavailableCache:
    available = False


class _ShortTermUnavailable:
    available = False


class _LongTermUnavailable:
    available = False


class _Memory:
    short_term = _ShortTermUnavailable()
    long_term = _LongTermUnavailable()


class _MessageChunk:
    def __init__(self, content: str):
        self.content = content


class _StreamingGraph:
    def __init__(self):
        self.ainvoke_called = False
        self.astream_events_called = False

    async def ainvoke(self, _state, config=None):
        self.ainvoke_called = True
        raise AssertionError("streaming graph should not use ainvoke fallback")

    async def astream_events(self, _state, config=None, version=None):
        from langchain_core.messages import AIMessage

        self.astream_events_called = True
        yield {
            "event": "on_chain_start",
            "name": "orchestrator",
            "data": {"output": {"next_agent": "billing_agent"}},
        }
        await asyncio.sleep(0)
        yield {
            "event": "on_chat_model_stream",
            "name": "billing_agent",
            "data": {"chunk": _MessageChunk("hel")},
        }
        await asyncio.sleep(0)
        yield {
            "event": "on_chat_model_stream",
            "name": "billing_agent",
            "data": {"chunk": _MessageChunk("lo")},
        }
        yield {
            "event": "on_chain_end",
            "name": "graph",
            "data": {"output": {"messages": [AIMessage(content="hello")]}},
        }


class _FallbackGraph:
    def __init__(self):
        self.ainvoke_called = False

    async def ainvoke(self, _state, config=None):
        from langchain_core.messages import AIMessage

        self.ainvoke_called = True
        return {"messages": [AIMessage(content="fallback ok")]}


def _payloads(chunks: list[str]) -> list[dict]:
    payloads = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def test_stream_chat_uses_native_graph_events_for_sse():
    async def _run():
        graph = _StreamingGraph()
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = graph

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_native_stream",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return graph, _payloads(chunks)

    graph, payloads = asyncio.run(_run())

    assert graph.astream_events_called is True
    assert graph.ainvoke_called is False
    assert [payload["event_type"] for payload in payloads] == [
        "stream_start",
        "route_decision",
        "message_delta",
        "message_delta",
        "final",
        "done",
    ]
    assert all(payload["schema_version"] == chat_service.SSE_SCHEMA_VERSION for payload in payloads)
    assert payloads[0]["stream_mode"] == "native"
    assert payloads[1]["step"] == "orchestrator"
    assert payloads[1]["route_to"] == "billing_agent"
    assert "".join(payload["content"] for payload in payloads if "content" in payload) == "hello"
    assert payloads[-1]["request_id"] == "req_native_stream"


def test_stream_chat_keeps_fallback_for_non_streaming_graphs():
    async def _run():
        graph = _FallbackGraph()
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = graph

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_fallback_stream",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return graph, _payloads(chunks)

    graph, payloads = asyncio.run(_run())

    assert graph.ainvoke_called is True
    assert payloads[0]["event_type"] == "stream_start"
    assert payloads[0]["stream_mode"] == "fallback"
    assert all(payload["schema_version"] == chat_service.SSE_SCHEMA_VERSION for payload in payloads)
    assert "fallback ok" in "".join(payload.get("content", "") for payload in payloads)
    assert payloads[-1]["event_type"] == "done"
    assert payloads[-1]["request_id"] == "req_fallback_stream"


def test_stream_chat_cache_hit_preserves_sse_contract():
    class _Cache:
        available = True

        async def get_cache(self, *_args, **_kwargs):
            return {
                "answer": "cached reply",
                "level": "L1_EXACT",
                "distance": 0.0,
            }

    async def _run():
        graph = _StreamingGraph()
        chat_service.semantic_cache = _Cache()
        chat_service.memory = _Memory()
        chat_service.graph = graph

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_cache_stream",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return graph, _payloads(chunks)

    graph, payloads = asyncio.run(_run())

    assert graph.astream_events_called is False
    assert [payload["event_type"] for payload in payloads] == [
        "stream_start",
        "route_decision",
        "message_delta",
        "final",
        "done",
    ]
    assert all(payload["schema_version"] == chat_service.SSE_SCHEMA_VERSION for payload in payloads)
    assert payloads[0]["stream_mode"] == "cache"
    assert payloads[1]["route_to"] == "semantic_cache"
    assert "".join(payload.get("content", "") for payload in payloads) == "cached reply"
    assert payloads[-1]["request_id"] == "req_cache_stream"


def test_init_agent_system_can_use_smoke_fake_graph(monkeypatch):
    class _Cache:
        available = False

        async def initialize(self):
            return None

    async def _run():
        original_graph = chat_service.graph
        original_memory = chat_service.memory
        original_cache = chat_service.semantic_cache
        monkeypatch.setenv("CLOUD_AGENT_SMOKE_FAKE_GRAPH", "true")
        chat_service.graph = None
        chat_service.memory = None
        chat_service.semantic_cache = _Cache()

        try:
            await chat_service.init_agent_system()
            chunks = []
            async for chunk in chat_service.stream_chat(
                "smoke query",
                "plain_user",
                "session_1",
                request_id="req_smoke_fake_graph",
                request_tenant_id="tenant_a",
            ):
                chunks.append(chunk)
            return chat_service.graph, chat_service.memory, _payloads(chunks)
        finally:
            chat_service.graph = original_graph
            chat_service.memory = original_memory
            chat_service.semantic_cache = original_cache

    graph, memory, payloads = asyncio.run(_run())

    assert graph.__class__.__name__ == "_SmokeGraph"
    assert memory.short_term.available is False
    assert memory.long_term.available is False
    assert [payload["event_type"] for payload in payloads] == [
        "stream_start",
        "route_decision",
        "agent_step",
        "message_delta",
        "message_delta",
        "final",
        "done",
    ]
    assert all(payload["schema_version"] == chat_service.SSE_SCHEMA_VERSION for payload in payloads)
    assert "".join(payload.get("content", "") for payload in payloads) == (
        "real backend smoke reply: smoke query"
    )
