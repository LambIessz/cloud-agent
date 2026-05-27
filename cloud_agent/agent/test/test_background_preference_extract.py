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


class _ShortTerm:
    available = True

    async def get_messages(self, *_args, **_kwargs):
        return []


class _LongTerm:
    available = True

    async def retrieve_relevant(self, *_args, **_kwargs):
        return []


class _Memory:
    def __init__(self):
        self.short_term = _ShortTerm()
        self.long_term = _LongTerm()
        self.saved = []
        self.background_calls = []

    async def save_conversation(self, user_id, session_id, turn):
        self.saved.append((user_id, session_id, turn))

    async def background_extract(self, user_id, session_id, llm, **_kwargs):
        self.background_calls.append((user_id, session_id, llm))
        return ["preference: concise"]


class _FailingBackgroundMemory(_Memory):
    async def background_extract(self, *_args, **_kwargs):
        raise RuntimeError("background secret leaked")


class _Graph:
    async def ainvoke(self, _state, config=None):
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content="ok")]}


def _event_log_events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[EventLog] "):
            events.append(json.loads(line.removeprefix("[EventLog] ")))
    return events


async def _consume_stream(query: str):
    chunks = []
    async for chunk in chat_service.stream_chat(
        query,
        "plain_user",
        "session_1",
        request_id="req_bg_extract",
        request_tenant_id="tenant_a",
    ):
        chunks.append(chunk)
    return chunks


def test_web_stream_triggers_background_preference_extract_every_n_turns(monkeypatch):
    memory = _Memory()
    chat_service.semantic_cache = _UnavailableCache()
    chat_service.memory = memory
    chat_service.graph = _Graph()
    chat_service._session_turn_counts.clear()
    chat_service._background_extract_tasks.clear()
    monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "true")
    monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_TURNS", "2")
    monkeypatch.setattr(chat_service, "_build_preference_extraction_llm", lambda: "fake_llm")

    async def _run():
        await _consume_stream("第一轮")
        await _consume_stream("第二轮")
        if chat_service._background_extract_tasks:
            await asyncio.gather(
                *list(chat_service._background_extract_tasks),
                return_exceptions=True,
            )

    asyncio.run(_run())

    assert len(memory.saved) == 2
    assert len(memory.background_calls) == 1
    user_id, session_id, llm = memory.background_calls[0]
    assert user_id == "plain_user"
    assert session_id.endswith(":session_1")
    assert llm == "fake_llm"


def test_background_preference_extract_emits_success_event(monkeypatch, capsys):
    memory = _Memory()
    chat_service.memory = memory
    monkeypatch.setattr(chat_service, "_build_preference_extraction_llm", lambda: "fake_llm")

    asyncio.run(
        chat_service._run_background_extract(
            "plain_user",
            "tenant_a:hash:session_1",
            request_id="req_bg_success",
            user_id_hash="hash_bg",
            tenant_id="tenant_a",
        )
    )

    output = capsys.readouterr().out
    events = _event_log_events(output)
    assert "plain_user" not in output
    assert "preference: concise" not in output
    assert events == [
        {
            "event_type": "background_extract",
            "request_id": "req_bg_success",
            "user_id_hash": "hash_bg",
            "tenant_id": "tenant_a",
            "component": "memory",
            "operation": "background_preference_extract",
            "status": "success",
            "extracted_count": 1,
        }
    ]


def test_background_preference_extract_emits_skipped_when_memory_missing(capsys):
    chat_service.memory = None

    asyncio.run(
        chat_service._run_background_extract(
            "plain_user",
            "tenant_a:hash:session_1",
            request_id="req_bg_skipped",
            user_id_hash="hash_bg",
            tenant_id="tenant_a",
        )
    )

    output = capsys.readouterr().out
    events = _event_log_events(output)
    assert "plain_user" not in output
    assert events[0]["event_type"] == "background_extract"
    assert events[0]["request_id"] == "req_bg_skipped"
    assert events[0]["status"] == "skipped"


def test_background_preference_extract_emits_degraded_without_error_message(monkeypatch, capsys):
    chat_service.memory = _FailingBackgroundMemory()
    monkeypatch.setattr(chat_service, "_build_preference_extraction_llm", lambda: "fake_llm")

    asyncio.run(
        chat_service._run_background_extract(
            "plain_user",
            "tenant_a:hash:session_1",
            request_id="req_bg_degraded",
            user_id_hash="hash_bg",
            tenant_id="tenant_a",
        )
    )

    output = capsys.readouterr().out
    events = _event_log_events(output)
    assert "plain_user" not in output
    assert "background secret leaked" not in output
    assert events[0]["event_type"] == "background_extract"
    assert events[0]["request_id"] == "req_bg_degraded"
    assert events[0]["status"] == "degraded"
    assert events[0]["error_type"] == "RuntimeError"


def test_web_stream_can_disable_background_preference_extract(monkeypatch):
    memory = _Memory()
    chat_service.semantic_cache = _UnavailableCache()
    chat_service.memory = memory
    chat_service.graph = _Graph()
    chat_service._session_turn_counts.clear()
    chat_service._background_extract_tasks.clear()
    monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED", "false")
    monkeypatch.setenv("CLOUD_AGENT_BACKGROUND_EXTRACT_TURNS", "1")
    monkeypatch.setattr(chat_service, "_build_preference_extraction_llm", lambda: "fake_llm")

    asyncio.run(_consume_stream("第一轮"))

    assert len(memory.saved) == 1
    assert memory.background_calls == []
