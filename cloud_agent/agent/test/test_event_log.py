import asyncio
import json
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
AGENT_DIR = Path(__file__).resolve().parents[1]
for path in (APP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import service.chat_service as chat_service
from core.workflow.event_log import build_event, emit_event


class _UnavailableCache:
    available = False


class _MissCache:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        return None


class _HitCache:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        return {
            "answer": "cached ok",
            "level": "user_exact",
            "distance": 0.01,
            "matched_question": "secret matched question",
        }


class _HitCacheWithMetadata:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        return {
            "answer": "cached ok",
            "level": "user_exact",
            "distance": 0.01,
            "matched_question": "secret matched question",
            "estimated_prompt_tokens": 120,
            "estimated_completion_tokens": 80,
            "estimated_cost_usd": 0.00014,
            "model": "qwen-plus",
        }


class _FailingCache:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        raise RuntimeError("cache failure leaked")


class _WritableCache:
    available = True

    def __init__(self):
        self.writes = []

    async def get_cache(self, *_args, **_kwargs):
        return None

    async def set_cache(self, *args, **kwargs):
        self.writes.append((args, kwargs))


class _FailingWritableCache:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        return None

    async def set_cache(self, *_args, **_kwargs):
        raise RuntimeError("cache write failure leaked")


class _ShortTermUnavailable:
    available = False


class _ShortTermAvailable:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        return []


class _ShortTermWithHistory:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        return [
            {"role": "user", "content": "secret history"},
            {"role": "assistant", "content": "secret answer"},
        ]


class _FailingShortTerm:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        raise ConnectionError("redis retrieve leaked")


class _LongTermUnavailable:
    available = False


class _LongTermWithPrefs:
    available = True

    async def retrieve_relevant(self, *_args, **_kwargs):
        return ["secret preference"]


class _FailingLongTerm:
    available = True

    async def retrieve_relevant(self, *_args, **_kwargs):
        raise RuntimeError("milvus retrieve leaked")


class _Memory:
    short_term = _ShortTermUnavailable()
    long_term = _LongTermUnavailable()


class _SaveMemory:
    def __init__(self, *, fail_save=False):
        self.short_term = _ShortTermAvailable()
        self.long_term = _LongTermUnavailable()
        self.fail_save = fail_save

    async def save_conversation(self, *_args, **_kwargs):
        if self.fail_save:
            raise TimeoutError("save failure leaked")


class _RetrieveMemory:
    def __init__(self, short_term, long_term):
        self.short_term = short_term
        self.long_term = long_term

    async def save_conversation(self, *_args, **_kwargs):
        pass


class _Graph:
    async def ainvoke(self, _state, config=None):
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content="ok")]}


class _UsageMessage:
    content = "cached write response"
    response_metadata = {
        "model_name": "qwen-plus",
        "token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
        },
    }
    usage_metadata = {}


class _GraphWithUsage:
    async def ainvoke(self, _state, config=None):
        return {"messages": [_UsageMessage()]}


class _FailingGraph:
    async def ainvoke(self, _state, config=None):
        raise RuntimeError("graph failure leaked")


class _TraceSpan:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.attributes = {}
        self.errors = []
        self.entered = False
        self.exited = False
        self.success = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, _exc, _tb):
        self.exited = True
        if exc_type is not None and not self.errors:
            self.set_error(exc_type.__name__)
        return False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_success(self):
        self.success = True
        self.attributes["request.status"] = "success"

    def set_error(self, error_type):
        self.errors.append(error_type)
        self.attributes["request.status"] = "error"
        self.attributes["error.type"] = error_type


def _event_log_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            event = json.loads(line.removeprefix("[EventLog] "))
            if event["event_type"] != "degradation":
                events.append(event)
    return events


def _degradation_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[Degradation] "):
            events.append(json.loads(line.removeprefix("[Degradation] ")))
    return events


async def _drain_cache_write_tasks():
    if chat_service._semantic_cache_write_tasks:
        await asyncio.gather(*list(chat_service._semantic_cache_write_tasks))


def test_emit_event_outputs_structured_json(capsys):
    emit_event(
        build_event(
            event_type="request_start",
            request_id="req_event",
            user_id_hash="hash_event",
            tenant_id="tenant_a",
            component="chat_service",
            operation="stream_chat",
        )
    )

    events = _event_log_events(capsys.readouterr().out)
    assert events == [
        {
            "event_type": "request_start",
            "request_id": "req_event",
            "user_id_hash": "hash_event",
            "tenant_id": "tenant_a",
            "component": "chat_service",
            "operation": "stream_chat",
        }
    ]


def test_stream_chat_emits_request_start_and_end_without_plain_user_id(capsys):
    async def _run():
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = _Graph()

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_stream_event",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())
    output = capsys.readouterr().out
    events = _event_log_events(output)

    assert "plain_user" not in output
    assert any("req_stream_event" in chunk for chunk in chunks)
    assert [event["event_type"] for event in events] == [
        "request_start",
        "cache_lookup",
        "memory_retrieve",
        "memory_retrieve",
        "memory_save",
        "request_end",
    ]
    assert all(event["request_id"] == "req_stream_event" for event in events)
    assert all(event["tenant_id"] == "tenant_a" for event in events)
    assert events[0]["component"] == "chat_service"
    assert events[0]["operation"] == "stream_chat"
    assert events[1]["component"] == "semantic_cache"
    assert events[1]["operation"] == "get_cache"
    assert events[1]["status"] == "unavailable"
    assert events[2]["component"] == "redis"
    assert events[2]["operation"] == "short_memory_get"
    assert events[2]["status"] == "unavailable"
    assert events[3]["component"] == "milvus"
    assert events[3]["operation"] == "long_memory_retrieve"
    assert events[3]["status"] == "unavailable"
    assert events[4]["component"] == "redis"
    assert events[4]["operation"] == "short_memory_save"
    assert events[4]["status"] == "unavailable"
    assert events[5]["component"] == "chat_service"
    assert events[5]["operation"] == "stream_chat"
    assert events[0]["user_id_hash"] == events[5]["user_id_hash"]
    assert events[5]["status"] == "success"
    assert isinstance(events[5]["latency_ms"], int)


def test_stream_chat_emits_request_end_error_without_error_message(capsys):
    async def _run():
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = _FailingGraph()

        async for _chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_stream_error",
            request_tenant_id="tenant_a",
        ):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(_run())

    output = capsys.readouterr().out
    events = _event_log_events(output)

    assert "plain_user" not in output
    assert "graph failure leaked" not in output
    assert [event["event_type"] for event in events] == [
        "request_start",
        "cache_lookup",
        "memory_retrieve",
        "memory_retrieve",
        "request_end",
    ]
    assert events[1]["status"] == "unavailable"
    assert events[2]["status"] == "unavailable"
    assert events[3]["status"] == "unavailable"
    assert events[4]["request_id"] == "req_stream_error"
    assert events[4]["status"] == "error"
    assert events[4]["error_type"] == "RuntimeError"
    assert isinstance(events[4]["latency_ms"], int)


def test_stream_chat_trace_span_success_has_no_sensitive_attributes(monkeypatch, capsys):
    spans = []

    def _start_span(**kwargs):
        span = _TraceSpan(kwargs)
        spans.append(span)
        return span

    async def _run():
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = _Graph()
        monkeypatch.setattr(chat_service, "start_stream_chat_span", _start_span)

        async for _chunk in chat_service.stream_chat(
            "secret query",
            "plain_user",
            "session_1",
            request_id="req_trace_success",
            request_tenant_id="tenant_a",
        ):
            pass

    asyncio.run(_run())
    output = capsys.readouterr().out

    assert len(spans) == 1
    span = spans[0]
    assert span.entered is True
    assert span.exited is True
    assert span.success is True
    assert span.errors == []
    assert span.attributes["cache.status"] == "unavailable"
    assert span.kwargs == {
        "identity_source": "debug_request",
        "request_id": "req_trace_success",
    }
    forbidden_values = (
        "plain_user",
        "tenant_a",
        "secret query",
        "session_1",
    )
    for value in forbidden_values:
        assert value not in json.dumps(span.kwargs, ensure_ascii=False)
        assert value not in json.dumps(span.attributes, ensure_ascii=False)
    assert "req_trace_success" not in json.dumps(span.attributes, ensure_ascii=False)
    assert "plain_user" not in output
    assert "secret query" not in output


def test_stream_chat_trace_span_error_uses_error_type_only(monkeypatch, capsys):
    spans = []

    def _start_span(**kwargs):
        span = _TraceSpan(kwargs)
        spans.append(span)
        return span

    async def _run():
        chat_service.semantic_cache = _UnavailableCache()
        chat_service.memory = _Memory()
        chat_service.graph = _FailingGraph()
        monkeypatch.setattr(chat_service, "start_stream_chat_span", _start_span)

        async for _chunk in chat_service.stream_chat(
            "secret query",
            "plain_user",
            "session_1",
            request_id="req_trace_error",
            request_tenant_id="tenant_a",
        ):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(_run())

    output = capsys.readouterr().out
    assert len(spans) == 1
    span = spans[0]
    assert span.entered is True
    assert span.exited is True
    assert span.success is False
    assert span.errors == ["RuntimeError"]
    assert span.attributes["request.status"] == "error"
    assert span.attributes["error.type"] == "RuntimeError"
    assert "graph failure leaked" not in json.dumps(span.attributes, ensure_ascii=False)
    assert "graph failure leaked" not in output


def test_stream_chat_emits_cache_hit_without_matched_question(capsys):
    async def _run():
        chat_service.semantic_cache = _HitCache()
        chat_service.memory = _Memory()
        chat_service.graph = _FailingGraph()

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_cache_hit",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())
    output = capsys.readouterr().out
    events = _event_log_events(output)

    assert "plain_user" not in output
    assert "secret matched question" not in output
    assert "cache" in "".join(chunks)
    assert events[1]["event_type"] == "cache_lookup"
    assert events[1]["status"] == "hit"
    assert events[1]["cache_level"] == "user_exact"
    assert events[1]["cache_distance"] == 0.01
    assert "matched_question" not in events[1]
    assert events[2]["event_type"] == "cache_benefit"
    assert events[2]["component"] == "semantic_cache"
    assert events[2]["operation"] == "stream_chat"
    assert events[2]["status"] == "estimated"
    assert events[2]["estimated_saved_calls"] == 1
    assert "matched_question" not in events[2]
    assert "estimated_saved_prompt_tokens" not in events[2]
    assert "estimated_saved_completion_tokens" not in events[2]
    assert "estimated_saved_cost_usd" not in events[2]


def test_stream_chat_cache_hit_with_metadata_emits_saved_token_and_cost(capsys):
    async def _run():
        chat_service.semantic_cache = _HitCacheWithMetadata()
        chat_service.memory = _Memory()
        chat_service.graph = _FailingGraph()

        chunks = []
        async for chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id="req_cache_hit_metadata",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_run())
    output = capsys.readouterr().out
    events = _event_log_events(output)

    assert "plain_user" not in output
    assert "secret matched question" not in output
    assert "cache" in "".join(chunks)
    assert events[2]["event_type"] == "cache_benefit"
    assert events[2]["status"] == "estimated"
    assert events[2]["estimated_saved_calls"] == 1
    assert events[2]["estimated_saved_prompt_tokens"] == 120
    assert events[2]["estimated_saved_completion_tokens"] == 80
    assert events[2]["estimated_saved_cost_usd"] == 0.00014
    assert "matched_question" not in events[2]
    assert "model" not in events[2]


def test_stream_chat_emits_memory_save_success_and_degraded_events(capsys):
    async def _run(memory, request_id):
        chat_service.semantic_cache = _HitCache()
        chat_service.memory = memory
        chat_service.graph = _FailingGraph()

        async for _chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id=request_id,
            request_tenant_id="tenant_a",
        ):
            pass

    asyncio.run(_run(_SaveMemory(), "req_memory_save"))
    success_events = _event_log_events(capsys.readouterr().out)
    assert success_events[2]["event_type"] == "cache_benefit"
    assert success_events[3]["event_type"] == "memory_save"
    assert success_events[3]["request_id"] == "req_memory_save"
    assert success_events[3]["component"] == "redis"
    assert success_events[3]["operation"] == "short_memory_save"
    assert success_events[3]["status"] == "success"

    asyncio.run(_run(_SaveMemory(fail_save=True), "req_memory_degraded"))
    degraded_output = capsys.readouterr().out
    degraded_events = _event_log_events(degraded_output)
    assert "plain_user" not in degraded_output
    assert "save failure leaked" not in degraded_output
    assert degraded_events[2]["event_type"] == "cache_benefit"
    assert degraded_events[3]["event_type"] == "memory_save"
    assert degraded_events[3]["request_id"] == "req_memory_degraded"
    assert degraded_events[3]["status"] == "degraded"
    assert degraded_events[3]["error_type"] == "TimeoutError"


def test_stream_chat_emits_memory_retrieve_success_and_degraded_events(capsys):
    async def _run(memory, request_id):
        chat_service.semantic_cache = _MissCache()
        chat_service.memory = memory
        chat_service.graph = _Graph()

        async for _chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id=request_id,
            request_tenant_id="tenant_a",
        ):
            pass

    asyncio.run(
        _run(
            _RetrieveMemory(_ShortTermWithHistory(), _LongTermWithPrefs()),
            "req_memory_retrieve",
        )
    )
    success_output = capsys.readouterr().out
    success_events = _event_log_events(success_output)
    assert "plain_user" not in success_output
    assert "secret history" not in success_output
    assert "secret answer" not in success_output
    assert "secret preference" not in success_output
    assert success_events[2]["event_type"] == "memory_retrieve"
    assert success_events[2]["component"] == "redis"
    assert success_events[2]["operation"] == "short_memory_get"
    assert success_events[2]["status"] == "success"
    assert success_events[2]["retrieved_count"] == 2
    assert success_events[3]["event_type"] == "memory_retrieve"
    assert success_events[3]["component"] == "milvus"
    assert success_events[3]["operation"] == "long_memory_retrieve"
    assert success_events[3]["status"] == "success"
    assert success_events[3]["retrieved_count"] == 1

    asyncio.run(
        _run(
            _RetrieveMemory(_FailingShortTerm(), _FailingLongTerm()),
            "req_memory_retrieve_degraded",
        )
    )
    degraded_output = capsys.readouterr().out
    degraded_events = _event_log_events(degraded_output)
    assert "plain_user" not in degraded_output
    assert "redis retrieve leaked" not in degraded_output
    assert "milvus retrieve leaked" not in degraded_output
    assert degraded_events[2]["event_type"] == "memory_retrieve"
    assert degraded_events[2]["status"] == "degraded"
    assert degraded_events[2]["error_type"] == "ConnectionError"
    assert degraded_events[3]["event_type"] == "memory_retrieve"
    assert degraded_events[3]["status"] == "degraded"
    assert degraded_events[3]["error_type"] == "RuntimeError"


def test_stream_chat_emits_cache_miss_and_degraded_events(capsys):
    async def _run(cache, request_id):
        chat_service.semantic_cache = cache
        chat_service.memory = _Memory()
        chat_service.graph = _Graph()

        async for _chunk in chat_service.stream_chat(
            "query",
            "plain_user",
            "session_1",
            request_id=request_id,
            request_tenant_id="tenant_a",
        ):
            pass

    asyncio.run(_run(_MissCache(), "req_cache_miss"))
    miss_events = _event_log_events(capsys.readouterr().out)
    assert miss_events[1]["event_type"] == "cache_lookup"
    assert miss_events[1]["status"] == "miss"
    assert "cache_level" not in miss_events[1]
    assert "cache_distance" not in miss_events[1]

    asyncio.run(_run(_FailingCache(), "req_cache_degraded"))
    degraded_output = capsys.readouterr().out
    degraded_events = _event_log_events(degraded_output)
    assert "cache failure leaked" not in degraded_output
    assert degraded_events[1]["event_type"] == "cache_lookup"
    assert degraded_events[1]["status"] == "degraded"
    assert degraded_events[1]["error_type"] == "RuntimeError"


def test_stream_chat_runtime_writes_user_scoped_semantic_cache_with_metadata(capsys):
    async def _run():
        cache = _WritableCache()
        chat_service.semantic_cache = cache
        chat_service.memory = _Memory()
        chat_service.graph = _GraphWithUsage()

        chunks = []
        async for chunk in chat_service.stream_chat(
            "secret runtime query",
            "plain_user",
            "session_1",
            request_id="req_cache_write",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        await _drain_cache_write_tasks()
        return cache, chunks

    cache, chunks = asyncio.run(_run())
    output = capsys.readouterr().out
    events = _event_log_events(output)

    assert "plain_user" not in output
    assert "secret runtime query" not in output
    assert any('"done": true' in chunk for chunk in chunks)
    assert events[1]["event_type"] == "cache_lookup"
    assert events[1]["status"] == "miss"

    assert len(cache.writes) == 1
    args, kwargs = cache.writes[0]
    assert args == ("secret runtime query", "cached write response")
    assert kwargs["user_id"] == "plain_user"
    assert kwargs["estimated_prompt_tokens"] == 100
    assert kwargs["estimated_completion_tokens"] == 20
    assert kwargs["estimated_cost_usd"] is None
    assert kwargs["model"] == "qwen-plus"
    assert kwargs["raise_on_error"] is True


def test_stream_chat_runtime_cache_write_failure_emits_degradation_without_message(capsys):
    async def _run():
        chat_service.semantic_cache = _FailingWritableCache()
        chat_service.memory = _Memory()
        chat_service.graph = _GraphWithUsage()

        async for _chunk in chat_service.stream_chat(
            "secret runtime query",
            "plain_user",
            "session_1",
            request_id="req_cache_write_fail",
            request_tenant_id="tenant_a",
        ):
            pass
        await _drain_cache_write_tasks()

    asyncio.run(_run())
    output = capsys.readouterr().out
    degradations = _degradation_events(output)

    assert "plain_user" not in output
    assert "secret runtime query" not in output
    assert "cache write failure leaked" not in output
    cache_write_degradations = [
        event for event in degradations
        if event["component"] == "semantic_cache" and event["operation"] == "set_cache"
    ]
    assert cache_write_degradations == [
        {
            "component": "semantic_cache",
            "error_type": "RuntimeError",
            "event_type": "degradation",
            "operation": "set_cache",
            "request_id": "req_cache_write_fail",
            "status": "degraded",
            "user_id_hash": "af4782fd6435ee69",
        }
    ]
