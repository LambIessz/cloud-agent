import sys
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_APP_MAIN_SPEC = importlib.util.spec_from_file_location(
    "deep_research_app_main_under_test",
    APP_DIR / "app_main.py",
)
assert _APP_MAIN_SPEC is not None
assert _APP_MAIN_SPEC.loader is not None
_APP_MAIN = importlib.util.module_from_spec(_APP_MAIN_SPEC)
_APP_MAIN_SPEC.loader.exec_module(_APP_MAIN)
app = _APP_MAIN.app
from backend.service import get_workflow_service
from backend.security.auth import resolve_authenticated_identity


class _FakeWorkflowService:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return "final answer"

    async def stream_events(self, **kwargs):  # pragma: no cover - not used in this test
        self.calls.append(kwargs)
        if False:
            yield {}


def test_local_mode_uses_default_identity_when_headers_are_absent(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_AUTH_MODE", raising=False)
    identity = resolve_authenticated_identity({})

    assert identity.user_id == "default_user"
    assert identity.tenant_id == "default_tenant"


def test_gateway_headers_override_request_body_identity(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_AUTH_MODE", "production")
    monkeypatch.setenv("DEEP_RESEARCH_AUTH_STRATEGY", "gateway")

    fake_service = _FakeWorkflowService()
    app.dependency_overrides[get_workflow_service] = lambda: fake_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/research/run",
                headers={
                    "X-Authenticated-User-Id": "gateway_user",
                    "X-Authenticated-Tenant-Id": "gateway_tenant",
                },
                json={
                    "query": "企业级 AI 应用规划",
                    "user_id": "body_user",
                    "tenant_id": "body_tenant",
                    "thread_id": "thread_1",
                },
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["user_id"] == "gateway_user"
        assert payload["tenant_id"] == "gateway_tenant"
        assert fake_service.calls == [
            {
                "query": "企业级 AI 应用规划",
                "user_id": "gateway_user",
                "thread_id": "thread_1",
                "tenant_id": "gateway_tenant",
                "max_iterations": None,
                "enable_memory": None,
            }
        ]
    finally:
        app.dependency_overrides.clear()
