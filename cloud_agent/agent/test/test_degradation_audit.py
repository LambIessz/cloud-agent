import asyncio
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import service.chat_service as chat_service


class _UnavailableCache:
    available = False

    async def get_cache(self, *_args, **_kwargs):
        raise AssertionError("unavailable cache should not be called")


class _FailingCache:
    available = True

    async def get_cache(self, *_args, **_kwargs):
        raise RuntimeError("cache backend leaked")


class _ShortTermUnavailable:
    available = False


class _LongTermUnavailable:
    available = False


class _FailingShortTerm:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        raise ConnectionError("redis leaked")


class _FailingLongTerm:
    available = True

    async def retrieve_relevant(self, *_args, **_kwargs):
        raise RuntimeError("milvus leaked")


class _Memory:
    def __init__(self, short_term, long_term, fail_save=False):
        self.short_term = short_term
        self.long_term = long_term
        self.fail_save = fail_save

    async def save_conversation(self, *_args, **_kwargs):
        if self.fail_save:
            raise TimeoutError("save leaked")


class _Graph:
    async def ainvoke(self, _state, config=None):
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content="ok")]}


def _degradation_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[Degradation] "):
            events.append(json.loads(line.removeprefix("[Degradation] ")))
    return events


def _event_log_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))
    return events


def _run_stream(cache, memory):
    async def _run():
        chat_service.semantic_cache = cache
        chat_service.memory = memory
        chat_service.graph = _Graph()

        chunks = []
        async for chunk in chat_service.stream_chat(
            "查账单",
            "plain_user",
            "session_1",
            request_id="req_degrade",
            request_tenant_id="tenant_a",
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(_run())


def test_unavailable_cache_and_memory_emit_degradation(capsys):
    chunks = _run_stream(
        _UnavailableCache(),
        _Memory(_ShortTermUnavailable(), _LongTermUnavailable()),
    )

    output = capsys.readouterr().out
    assert "plain_user" not in output
    assert any("req_degrade" in chunk for chunk in chunks)

    events = _degradation_events(output)
    assert {
        (event["component"], event["operation"], event["status"])
        for event in events
    } >= {
        ("semantic_cache", "get_cache", "unavailable"),
        ("redis", "short_memory_get", "unavailable"),
        ("milvus", "long_memory_retrieve", "unavailable"),
        ("redis", "short_memory_save", "unavailable"),
    }
    event_log_events = [
        event for event in _event_log_events(output) if event["event_type"] == "degradation"
    ]
    assert {
        (event["component"], event["operation"], event["status"])
        for event in event_log_events
    } >= {
        ("semantic_cache", "get_cache", "unavailable"),
        ("redis", "short_memory_get", "unavailable"),
        ("milvus", "long_memory_retrieve", "unavailable"),
        ("redis", "short_memory_save", "unavailable"),
    }


def test_backend_failures_emit_error_type_without_error_message(capsys):
    _run_stream(
        _FailingCache(),
        _Memory(_FailingShortTerm(), _FailingLongTerm(), fail_save=True),
    )

    output = capsys.readouterr().out
    assert "plain_user" not in output
    assert "cache backend leaked" not in output
    assert "redis leaked" not in output
    assert "milvus leaked" not in output
    assert "save leaked" not in output

    events = _degradation_events(output)
    assert {
        (event["component"], event["operation"], event.get("error_type"))
        for event in events
    } >= {
        ("semantic_cache", "get_cache", "RuntimeError"),
        ("redis", "short_memory_get", "ConnectionError"),
        ("milvus", "long_memory_retrieve", "RuntimeError"),
        ("redis", "short_memory_save", "TimeoutError"),
    }
    assert all(event["request_id"] == "req_degrade" for event in events)
    assert all(event["user_id_hash"] != "unknown" for event in events)
    event_log_events = [
        event for event in _event_log_events(output) if event["event_type"] == "degradation"
    ]
    assert {
        (event["component"], event["operation"], event.get("error_type"))
        for event in event_log_events
    } >= {
        ("semantic_cache", "get_cache", "RuntimeError"),
        ("redis", "short_memory_get", "ConnectionError"),
        ("milvus", "long_memory_retrieve", "RuntimeError"),
        ("redis", "short_memory_save", "TimeoutError"),
    }
    assert all(event["request_id"] == "req_degrade" for event in event_log_events)
    assert all(event["user_id_hash"] != "unknown" for event in event_log_events)
