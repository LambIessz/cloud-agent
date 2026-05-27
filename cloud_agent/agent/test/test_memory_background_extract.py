import asyncio
import json
from types import SimpleNamespace

from core.memory.memory_manager import MemoryManager


class _ShortTerm:
    def __init__(self, messages=None, error=None):
        self.messages = messages or []
        self.error = error

    async def get_messages(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.messages


class _LongTerm:
    def __init__(self, available=True, existing=None, error_on_save=None):
        self.available = available
        self.existing = existing or []
        self.error_on_save = error_on_save
        self.saved = []

    async def retrieve_relevant(self, *_args, **_kwargs):
        return self.existing

    async def save_memory(self, *, user_id, content, memory_type):
        if self.error_on_save:
            raise self.error_on_save
        self.saved.append(
            {
                "user_id": user_id,
                "content": content,
                "memory_type": memory_type,
            }
        )


class _LLM:
    def __init__(self, content):
        self.content = content

    async def ainvoke(self, *_args, **_kwargs):
        return SimpleNamespace(content=self.content)


def _memory(short_term, long_term):
    manager = MemoryManager.__new__(MemoryManager)
    manager.short_term = short_term
    manager.long_term = long_term
    return manager


def _events(output: str):
    events = []
    for line in output.splitlines():
        if line.startswith("[Degradation] "):
            events.append(json.loads(line.removeprefix("[Degradation] ")))
    return events


def _messages():
    return [
        {"role": "user", "content": "我喜欢简洁回答"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "我主要使用上海地域"},
        {"role": "assistant", "content": "已了解"},
    ]


def test_background_extract_returns_empty_list_when_long_term_unavailable():
    manager = _memory(_ShortTerm(_messages()), _LongTerm(available=False))

    result = asyncio.run(manager.background_extract("user_1", "session_1", _LLM("偏好: 简洁")))

    assert result == []


def test_background_extract_saves_new_preferences():
    long_term = _LongTerm()
    manager = _memory(_ShortTerm(_messages()), long_term)

    result = asyncio.run(
        manager.background_extract(
            "user_1",
            "session_1",
            _LLM("偏好: 简洁回答\n地域: 上海"),
        )
    )

    assert result == ["偏好: 简洁回答", "地域: 上海"]
    assert long_term.saved == [
        {"user_id": "user_1", "content": "偏好: 简洁回答", "memory_type": "preference"},
        {"user_id": "user_1", "content": "地域: 上海", "memory_type": "preference"},
    ]


def test_background_extract_emits_degradation_without_error_message(capsys):
    manager = _memory(
        _ShortTerm(_messages()),
        _LongTerm(error_on_save=RuntimeError("milvus secret leaked")),
    )

    result = asyncio.run(
        manager.background_extract(
            "user_1",
            "session_1",
            _LLM("偏好: 简洁回答"),
            request_id="req_mem_bg",
            user_id_hash="hash_mem_bg",
        )
    )

    output = capsys.readouterr().out
    assert result == []
    assert "milvus secret leaked" not in output
    events = _events(output)
    assert len(events) == 1
    assert events[0]["request_id"] == "req_mem_bg"
    assert events[0]["user_id_hash"] == "hash_mem_bg"
    assert events[0]["component"] == "memory"
    assert events[0]["operation"] == "background_preference_extract"
    assert events[0]["error_type"] == "RuntimeError"
