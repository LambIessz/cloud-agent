import asyncio
import importlib.util
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_PATH = PROJECT_ROOT / "ops" / "memory_e2e_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("memory_e2e_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeShortTerm:
    def __init__(self, available=True):
        self.available = available
        self.cleared = False

    async def clear(self, user_id, session_id):
        self.cleared = True


class _FakeLongTerm:
    def __init__(self, available=True):
        self.available = available
        self._client = None


class _FakeMemory:
    def __init__(self, *, marker, short_available=True, long_available=True):
        self.marker = marker
        self.short_term = _FakeShortTerm(short_available)
        self.long_term = _FakeLongTerm(long_available)
        self.initialized = False
        self.closed = False
        self.messages = []
        self.extracted = []
        self.retrieval_queries = []

    async def initialize(self):
        self.initialized = True

    async def close(self):
        self.closed = True

    async def save_conversation(self, user_id, session_id, messages):
        self.messages.extend(messages)

    async def get_recent_messages(self, user_id, session_id):
        return list(self.messages)

    async def background_extract(self, user_id, session_id, llm, **_kwargs):
        response = await llm.ainvoke([])
        self.extracted = [response.content]
        return list(self.extracted)

    async def load_preferences(self, user_id, query, top_k=10):
        self.retrieval_queries.append(query)
        return [f"preference: {self.marker}"]


def test_memory_e2e_smoke_passes_with_injected_memory_manager():
    smoke = _load_smoke()
    marker = "memory-smoke-test-marker"
    fake = _FakeMemory(marker=marker)
    secret = "real-secret-value-that-must-not-print"

    report = asyncio.run(
        smoke.run_memory_smoke(
            env={
                "DEEPSEEK_API_KEY": secret,
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "true",
            },
            memory_factory=lambda env: fake,
            marker_factory=lambda: marker,
            retrieval_timeout=0.1,
        )
    )

    assert report.status == "ready"
    assert fake.initialized is True
    assert fake.closed is True
    assert fake.short_term.cleared is True
    assert fake.retrieval_queries == [marker]
    checks = {check.name: check.status for check in report.checks}
    assert checks["memory_config"] == smoke.PASS
    assert checks["short_term_roundtrip"] == smoke.PASS
    assert checks["background_extract"] == smoke.PASS
    assert checks["long_term_retrieval"] == smoke.PASS
    assert checks["cleanup"] == smoke.PASS

    rendered = smoke.format_text(report) + smoke.format_json(report)
    assert secret not in rendered
    assert "synthetic memory smoke marker" not in rendered


def test_memory_e2e_smoke_fails_when_memory_is_disabled():
    smoke = _load_smoke()

    report = asyncio.run(
        smoke.run_memory_smoke(
            env={"CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "false"},
            memory_factory=lambda env: _FakeMemory(marker="unused"),
        )
    )

    assert report.status == "failed"
    assert report.exit_code(strict=False) == 1
    assert report.checks[0].name == "memory_config"
    assert report.checks[0].status == smoke.FAIL


def test_memory_e2e_smoke_restores_process_environment(monkeypatch):
    smoke = _load_smoke()
    marker = "memory-smoke-env-marker"
    fake = _FakeMemory(marker=marker)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "original-key")
    monkeypatch.delenv("CLOUD_AGENT_VECTOR_SEARCH_ENABLED", raising=False)

    report = asyncio.run(
        smoke.run_memory_smoke(
            env={
                "DEEPSEEK_API_KEY": "temporary-key",
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "true",
            },
            memory_factory=lambda env: fake,
            marker_factory=lambda: marker,
        )
    )

    assert report.status == "ready"
    assert os.environ["DEEPSEEK_API_KEY"] == "original-key"
    assert "CLOUD_AGENT_VECTOR_SEARCH_ENABLED" not in os.environ


def test_memory_e2e_smoke_uses_isolated_milvus_lite_uri_by_default():
    smoke = _load_smoke()
    marker = "memory-smoke-uri-marker"
    fake = _FakeMemory(marker=marker)
    captured_env = {}

    def memory_factory(env):
        captured_env.update(env)
        return fake

    report = asyncio.run(
        smoke.run_memory_smoke(
            env={
                "CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "true",
                "CLOUD_AGENT_MILVUS_MODE": "lite",
            },
            memory_factory=memory_factory,
            marker_factory=lambda: marker,
        )
    )

    assert report.status == "ready"
    assert ".codex-run" in captured_env["CLOUD_AGENT_LONG_TERM_MEMORY_URI"]
    assert "memory-e2e-milvus-" in captured_env["CLOUD_AGENT_LONG_TERM_MEMORY_URI"]


def test_memory_e2e_smoke_fails_when_a_store_is_unavailable():
    smoke = _load_smoke()
    fake = _FakeMemory(marker="unused", short_available=False, long_available=True)

    report = asyncio.run(
        smoke.run_memory_smoke(
            env={"CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED": "true"},
            memory_factory=lambda env: fake,
        )
    )

    assert report.status == "failed"
    assert any(
        check.name == "short_term_redis" and check.status == smoke.FAIL
        for check in report.checks
    )
    assert fake.messages == []
    assert fake.closed is True


def test_memory_e2e_smoke_writes_json_artifact(tmp_path):
    smoke = _load_smoke()
    artifact = tmp_path / "memory-e2e-smoke.json"
    report = smoke.MemorySmokeReport([smoke.CheckResult("memory_config", smoke.PASS, "configured")])

    smoke.write_artifact(artifact, report)

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["checks"][0]["name"] == "memory_config"
